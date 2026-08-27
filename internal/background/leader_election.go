package background

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

type LeaderElector struct {
	pool    *pgxpool.Pool
	key     string
	holder  string
	ttl     time.Duration
}

func NewLeaderElector(pool *pgxpool.Pool, key, holder string, ttl time.Duration) *LeaderElector {
	return &LeaderElector{pool: pool, key: key, holder: holder, ttl: ttl}
}

func (le *LeaderElector) TryAcquire(ctx context.Context) (bool, error) {
	now := time.Now().UTC()
	expires := now.Add(le.ttl)
	tag, err := le.pool.Exec(ctx, `
		INSERT INTO leader_locks (lock_key, holder, expires_at, acquired_at)
		VALUES ($1, $2, $3, $3)
		ON CONFLICT (lock_key) DO UPDATE
		SET holder = EXCLUDED.holder, expires_at = EXCLUDED.expires_at, acquired_at = EXCLUDED.acquired_at
		WHERE leader_locks.expires_at < $3 OR leader_locks.holder = $2`,
		le.key, le.holder, expires)
	if err != nil {
		return false, fmt.Errorf("acquire leader lock: %w", err)
	}
	return tag.RowsAffected() > 0, nil
}

func (le *LeaderElector) Renew(ctx context.Context) (bool, error) {
	expires := time.Now().UTC().Add(le.ttl)
	tag, err := le.pool.Exec(ctx, `
		UPDATE leader_locks SET expires_at = $2
		WHERE lock_key = $1 AND holder = $3`,
		le.key, expires, le.holder)
	if err != nil {
		return false, err
	}
	return tag.RowsAffected() > 0, nil
}

func (le *LeaderElector) Release(ctx context.Context) error {
	_, err := le.pool.Exec(ctx, `DELETE FROM leader_locks WHERE lock_key = $1 AND holder = $2`, le.key, le.holder)
	return err
}

func (le *LeaderElector) RunAsLeader(ctx context.Context, interval time.Duration, fn func(context.Context) error) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	isLeader := false
	for {
		select {
		case <-ctx.Done():
			if isLeader {
				_ = le.Release(context.Background())
			}
			return
		case <-ticker.C:
			if !isLeader {
				acq, err := le.TryAcquire(ctx)
				if err != nil {
					slog.Warn("leader election acquire failed", "error", err)
					continue
				}
				if acq {
					isLeader = true
					slog.Info("acquired leader lock", "key", le.key, "holder", le.holder)
				}
			} else {
				ok, err := le.Renew(ctx)
				if err != nil || !ok {
					isLeader = false
					slog.Warn("lost leader lock", "key", le.key, "error", err)
					continue
				}
			}
			if isLeader {
				if err := fn(ctx); err != nil {
					slog.Error("leader work error", "key", le.key, "error", err)
				}
			}
		}
	}
}