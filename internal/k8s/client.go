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

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
)

type Client struct {
	ClientSet *kubernetes.Clientset
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
		GracePeriodSeconds: ptr(int64(300)),
	})
	if err != nil && !errors.IsNotFound(err) {
		return err
	}
	return nil
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
	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: c.Namespace,
			Labels:    labels,
		},
		Data: data,
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