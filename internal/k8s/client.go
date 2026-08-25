// Copyright 2026 Mael Klingler
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package k8s

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
)

type Client struct {
	ClientSet kubernetes.Interface
	Namespace string
}

func NewClient(namespace string) (*Client, error) {
	config, err := rest.InClusterConfig()
	if err != nil {
		config, err = clientcmd.BuildConfigFromFlags("", clientcmd.RecommendedHomeFile)
		if err != nil {
			return nil, fmt.Errorf("could not get k8s config: %w", err)
		}
	}

	clientSet, err := kubernetes.NewForConfig(config)
	if err != nil {
		return nil, fmt.Errorf("could not create k8s client: %w", err)
	}

	return &Client{
		ClientSet: clientSet,
		Namespace: namespace,
	}, nil
}

func (c *Client) GetPod(ctx context.Context, name string) (*corev1.Pod, error) {
	pod, err := c.ClientSet.CoreV1().Pods(c.Namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		if errors.IsNotFound(err) {
			return nil, nil
		}
		return nil, err
	}
	return pod, nil
}

func (c *Client) GetPodPhase(ctx context.Context, name string) (string, error) {
	pod, err := c.GetPod(ctx, name)
	if err != nil {
		return "", err
	}
	if pod == nil {
		return "", nil
	}
	return string(pod.Status.Phase), nil
}

func (c *Client) ListPods(ctx context.Context, labelSelector string) ([]corev1.Pod, error) {
	listOpts := metav1.ListOptions{}
	if labelSelector != "" {
		listOpts.LabelSelector = labelSelector
	}
	result, err := c.ClientSet.CoreV1().Pods(c.Namespace).List(ctx, listOpts)
	if err != nil {
		return nil, err
	}
	return result.Items, nil
}

func (c *Client) DeletePod(ctx context.Context, name string) error {
	err := c.ClientSet.CoreV1().Pods(c.Namespace).Delete(ctx, name, metav1.DeleteOptions{
		GracePeriodSeconds:     ptr(int64(300)),
		PropagationPolicy:      ptr(metav1.DeletePropagationForeground),
	})
	if err != nil && !errors.IsNotFound(err) {
		return err
	}
	return nil
}

// WaitForPodDeletion polls until the pod is gone or timeout expires.
func (c *Client) WaitForPodDeletion(ctx context.Context, name string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		pod, err := c.GetPod(ctx, name)
		if err != nil {
			return err
		}
		if pod == nil {
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(500 * time.Millisecond):
		}
	}
	return fmt.Errorf("pod %s still exists after %s", name, timeout)
}

func (c *Client) CreatePod(ctx context.Context, pod *corev1.Pod) (*corev1.Pod, error) {
	created, err := c.ClientSet.CoreV1().Pods(c.Namespace).Create(ctx, pod, metav1.CreateOptions{})
	if err != nil {
		return nil, err
	}
	return created, nil
}

func (c *Client) CreateConfigMap(ctx context.Context, name string, data map[string]string, labels map[string]string) (*corev1.ConfigMap, error) {
	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: c.Namespace,
			Labels:    labels,
		},
		Data: data,
	}
	created, err := c.ClientSet.CoreV1().ConfigMaps(c.Namespace).Create(ctx, cm, metav1.CreateOptions{})
	if err != nil {
		if errors.IsAlreadyExists(err) {
			return c.ReplaceConfigMap(ctx, name, data, labels)
		}
		return nil, err
	}
	return created, nil
}

func (c *Client) ReplaceConfigMap(ctx context.Context, name string, data map[string]string, labels map[string]string) (*corev1.ConfigMap, error) {
	existing, err := c.GetConfigMap(ctx, name)
	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: c.Namespace,
			Labels:    labels,
		},
		Data: data,
	}
	if existing != nil {
		cm.ResourceVersion = existing.ResourceVersion
	}
	updated, err := c.ClientSet.CoreV1().ConfigMaps(c.Namespace).Update(ctx, cm, metav1.UpdateOptions{})
	if err != nil {
		return nil, err
	}
	return updated, nil
}

func (c *Client) DeleteConfigMap(ctx context.Context, name string) error {
	err := c.ClientSet.CoreV1().ConfigMaps(c.Namespace).Delete(ctx, name, metav1.DeleteOptions{})
	if err != nil && !errors.IsNotFound(err) {
		return err
	}
	return nil
}

func (c *Client) GetConfigMap(ctx context.Context, name string) (*corev1.ConfigMap, error) {
	cm, err := c.ClientSet.CoreV1().ConfigMaps(c.Namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		if errors.IsNotFound(err) {
			return nil, nil
		}
		return nil, err
	}
	return cm, nil
}

func (c *Client) CreateSecret(ctx context.Context, name string, stringData map[string]string, secretType corev1.SecretType) (*corev1.Secret, error) {
	secret := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: c.Namespace,
		},
		Type:       secretType,
		StringData: stringData,
	}
	created, err := c.ClientSet.CoreV1().Secrets(c.Namespace).Create(ctx, secret, metav1.CreateOptions{})
	if err != nil {
		if errors.IsAlreadyExists(err) {
			return nil, nil
		}
		return nil, err
	}
	return created, nil
}

func (c *Client) GetSecret(ctx context.Context, name string) (*corev1.Secret, error) {
	secret, err := c.ClientSet.CoreV1().Secrets(c.Namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		if errors.IsNotFound(err) {
			return nil, nil
		}
		return nil, err
	}
	return secret, nil
}

func (c *Client) GetPodLogs(ctx context.Context, name string, tailLines int64) (string, error) {
	opts := &corev1.PodLogOptions{
		TailLines: &tailLines,
	}
	req := c.ClientSet.CoreV1().Pods(c.Namespace).GetLogs(name, opts)
	logs, err := req.DoRaw(ctx)
	if err != nil {
		if errors.IsNotFound(err) {
			return "", nil
		}
		return "", err
	}
	return string(logs), nil
}

// GetPodIP returns the pod IP of a running pod, or empty string if not found
// or not yet assigned.
func (c *Client) GetPodIP(ctx context.Context, name string) (string, error) {
	pod, err := c.GetPod(ctx, name)
	if err != nil {
		return "", err
	}
	if pod == nil {
		return "", nil
	}
	return pod.Status.PodIP, nil
}

func (c *Client) EnsureNamespace(ctx context.Context, name string) error {
	_, err := c.ClientSet.CoreV1().Namespaces().Get(ctx, name, metav1.GetOptions{})
	if err == nil {
		return nil
	}
	if !errors.IsNotFound(err) {
		return err
	}
	_, err = c.ClientSet.CoreV1().Namespaces().Create(ctx, &corev1.Namespace{
		ObjectMeta: metav1.ObjectMeta{Name: name},
	}, metav1.CreateOptions{})
	return err
}

// EnsureSecret creates a secret if it does not already exist. If it exists,
// it is left unchanged (secrets are managed externally or via EnsureSecrets).
func (c *Client) EnsureSecret(ctx context.Context, name string, stringData map[string]string, secretType corev1.SecretType) error {
	existing, err := c.GetSecret(ctx, name)
	if err != nil {
		return fmt.Errorf("check secret %s: %w", name, err)
	}
	if existing != nil {
		needUpdate := false
		for key, expected := range stringData {
			actual, ok := existing.Data[key]
			if !ok || string(actual) != expected {
				needUpdate = true
				break
			}
		}
		if needUpdate {
			slog.Warn("secret drift detected, updating", "secret", name)
			_, err := c.UpdateSecret(ctx, name, stringData, secretType)
			return err
		}
		return nil
	}
	_, err = c.CreateSecret(ctx, name, stringData, secretType)
	return err
}

func (c *Client) UpdateSecret(ctx context.Context, name string, stringData map[string]string, secretType corev1.SecretType) (*corev1.Secret, error) {
	secret := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: c.Namespace},
		Type:       secretType,
		StringData: stringData,
	}
	updated, err := c.ClientSet.CoreV1().Secrets(c.Namespace).Update(ctx, secret, metav1.UpdateOptions{})
	if err != nil {
		return nil, err
	}
	return updated, nil
}

// EnsureSecrets idempotently creates the set of secrets required by agent pods.
// This is called once at orchestrator startup (not per spawn) to avoid races
// when multiple agent pods are spawned concurrently.
func (c *Client) EnsureSecrets(ctx context.Context, params SecretParams) error {
	if params.GitLabToken != "" {
		if err := c.EnsureSecret(ctx, "gitlab-token", map[string]string{"token": params.GitLabToken}, corev1.SecretTypeOpaque); err != nil {
			return fmt.Errorf("ensure gitlab-token secret: %w", err)
		}
	}
	if params.GitHubToken != "" {
		if err := c.EnsureSecret(ctx, "github-token", map[string]string{"token": params.GitHubToken}, corev1.SecretTypeOpaque); err != nil {
			return fmt.Errorf("ensure github-token secret: %w", err)
		}
	}
	if params.OllamaCloudAPIKey != "" {
		if err := c.EnsureSecret(ctx, "ollama-cloud-api-key", map[string]string{"api-key": params.OllamaCloudAPIKey}, corev1.SecretTypeOpaque); err != nil {
			return fmt.Errorf("ensure ollama-cloud secret: %w", err)
		}
	}
	if params.OpenAIAPIKey != "" {
		if err := c.EnsureSecret(ctx, "openai-api-key", map[string]string{"api-key": params.OpenAIAPIKey}, corev1.SecretTypeOpaque); err != nil {
			return fmt.Errorf("ensure openai secret: %w", err)
		}
	}
	if params.AnthropicAPIKey != "" {
		if err := c.EnsureSecret(ctx, "anthropic-api-key", map[string]string{"api-key": params.AnthropicAPIKey}, corev1.SecretTypeOpaque); err != nil {
			return fmt.Errorf("ensure anthropic secret: %w", err)
		}
	}
	if params.HivemindAPIKey != "" {
		if err := c.EnsureSecret(ctx, "orchestrator-env", map[string]string{"HIVEMIND_API_KEY": params.HivemindAPIKey}, corev1.SecretTypeOpaque); err != nil {
			return fmt.Errorf("ensure orchestrator-env secret: %w", err)
		}
	}
	return nil
}

// SecretParams holds the secret values to ensure at startup.
type SecretParams struct {
	GitLabToken       string
	GitHubToken       string
	OllamaCloudAPIKey string
	OpenAIAPIKey      string
	AnthropicAPIKey   string
	HivemindAPIKey    string
}

func (c *Client) CleanupAgentResources(ctx context.Context, ticketID string) {
	podName := fmt.Sprintf("agent-worker-%s", strings.ToLower(ticketID))
	for _, suffix := range []string{"repos", "assignment", "opencode", "memory"} {
		cmName := fmt.Sprintf("%s-%s", podName, suffix)
		if err := c.DeleteConfigMap(ctx, cmName); err != nil {
			slog.Warn("failed to delete configmap", "name", cmName, "error", err)
		}
	}
	slog.Info("configmaps cleaned up", "pod", podName)
}

func ptr[T any](v T) *T { return &v }