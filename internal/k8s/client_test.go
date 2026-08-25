package k8s_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"

	"github.com/maelklingler/hivemind/internal/k8s"
)

func TestClient_CreateGetDeletePod(t *testing.T) {
	cs := fake.NewSimpleClientset()
	c := &k8s.Client{ClientSet: cs, Namespace: "hivemind"}
	ctx := context.Background()

	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: "test-pod", Namespace: "hivemind"},
		Spec:       corev1.PodSpec{Containers: []corev1.Container{{Name: "test", Image: "busybox"}}},
	}
	created, err := c.CreatePod(ctx, pod)
	require.NoError(t, err)
	assert.Equal(t, "test-pod", created.Name)

	got, err := c.GetPod(ctx, "test-pod")
	require.NoError(t, err)
	assert.NotNil(t, got)

	err = c.DeletePod(ctx, "test-pod")
	require.NoError(t, err)

	got, err = c.GetPod(ctx, "test-pod")
	require.NoError(t, err)
	assert.Nil(t, got)
}

func TestClient_CreateGetDeleteConfigMap(t *testing.T) {
	cs := fake.NewSimpleClientset()
	c := &k8s.Client{ClientSet: cs, Namespace: "hivemind"}
	ctx := context.Background()

	cm, err := c.CreateConfigMap(ctx, "test-cm", map[string]string{"key": "val"}, map[string]string{"app": "test"})
	require.NoError(t, err)
	assert.Equal(t, "test-cm", cm.Name)

	got, err := c.GetConfigMap(ctx, "test-cm")
	require.NoError(t, err)
	assert.Equal(t, "val", got.Data["key"])

	err = c.DeleteConfigMap(ctx, "test-cm")
	require.NoError(t, err)

	got, err = c.GetConfigMap(ctx, "test-cm")
	require.NoError(t, err)
	assert.Nil(t, got)
}

func TestClient_EnsureSecret_Idempotent(t *testing.T) {
	cs := fake.NewSimpleClientset()
	c := &k8s.Client{ClientSet: cs, Namespace: "hivemind"}
	ctx := context.Background()

	err := c.EnsureSecret(ctx, "test-secret", map[string]string{"token": "abc"}, corev1.SecretTypeOpaque)
	require.NoError(t, err)

	err = c.EnsureSecret(ctx, "test-secret", map[string]string{"token": "abc"}, corev1.SecretTypeOpaque)
	require.NoError(t, err)

	got, err := c.GetSecret(ctx, "test-secret")
	require.NoError(t, err)
	assert.NotNil(t, got)
}

func TestClient_EnsureSecret_Drift(t *testing.T) {
	cs := fake.NewSimpleClientset()
	c := &k8s.Client{ClientSet: cs, Namespace: "hivemind"}
	ctx := context.Background()

	_ = c.EnsureSecret(ctx, "test-secret2", map[string]string{"token": "old"}, corev1.SecretTypeOpaque)
	err := c.EnsureSecret(ctx, "test-secret2", map[string]string{"token": "new"}, corev1.SecretTypeOpaque)
	require.NoError(t, err)

	got, _ := c.GetSecret(ctx, "test-secret2")
	assert.NotNil(t, got)
}

func TestClient_EnsureNamespace(t *testing.T) {
	cs := fake.NewSimpleClientset()
	c := &k8s.Client{ClientSet: cs, Namespace: "hivemind"}
	ctx := context.Background()

	err := c.EnsureNamespace(ctx, "hivemind")
	require.NoError(t, err)
	err = c.EnsureNamespace(ctx, "hivemind")
	require.NoError(t, err)
}

func TestClient_ListPods(t *testing.T) {
	cs := fake.NewSimpleClientset(&corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: "p1", Namespace: "hivemind", Labels: map[string]string{"app": "test"}},
	})
	c := &k8s.Client{ClientSet: cs, Namespace: "hivemind"}
	ctx := context.Background()

	pods, err := c.ListPods(ctx, "app=test")
	require.NoError(t, err)
	assert.Len(t, pods, 1)
}

func TestClient_CleanupAgentResources(t *testing.T) {
	cs := fake.NewSimpleClientset()
	c := &k8s.Client{ClientSet: cs, Namespace: "hivemind"}
	ctx := context.Background()

	_, _ = c.CreateConfigMap(ctx, "agent-worker-test-repos", map[string]string{"d": "v"}, nil)
	_, _ = c.CreateConfigMap(ctx, "agent-worker-test-assignment", map[string]string{"d": "v"}, nil)
	_, _ = c.CreateConfigMap(ctx, "agent-worker-test-opencode", map[string]string{"d": "v"}, nil)
	_, _ = c.CreateConfigMap(ctx, "agent-worker-test-memory", map[string]string{"d": "v"}, nil)

	c.CleanupAgentResources(ctx, "TEST")

	for _, suffix := range []string{"repos", "assignment", "opencode", "memory"} {
		cm, _ := c.GetConfigMap(ctx, "agent-worker-test-"+suffix)
		assert.Nil(t, cm, "configmap %s should be deleted", suffix)
	}
}

func TestClient_GetPodIP(t *testing.T) {
	cs := fake.NewSimpleClientset(&corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: "pod-ip-test", Namespace: "hivemind"},
		Status:     corev1.PodStatus{PodIP: "10.0.0.1"},
	})
	c := &k8s.Client{ClientSet: cs, Namespace: "hivemind"}
	ctx := context.Background()

	ip, err := c.GetPodIP(ctx, "pod-ip-test")
	require.NoError(t, err)
	assert.Equal(t, "10.0.0.1", ip)
}