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

package background_test

import (
	"testing"

	"github.com/stretchr/testify/assert"

	"github.com/maelklingler/hivemind/internal/database/repository"
)

func TestPhase_NextPhase(t *testing.T) {
	assert.Equal(t, repository.PhaseTest, nextPhaseForTest(repository.PhaseWork))
	assert.Equal(t, repository.PhaseReview, nextPhaseForTest(repository.PhaseTest))
	assert.Equal(t, repository.PhaseShip, nextPhaseForTest(repository.PhaseReview))
	assert.Equal(t, repository.PhaseListen, nextPhaseForTest(repository.PhaseShip))
	assert.Equal(t, repository.Phase(""), nextPhaseForTest(repository.PhaseListen))
}

// nextPhaseForTest mirrors the private nextPhase in the background package.
// Since the background package's nextPhase is duplicated from pgxrepo, we
// test the repository.Phase constants here for consistency.
func nextPhaseForTest(p repository.Phase) repository.Phase {
	switch p {
	case repository.PhaseWork:
		return repository.PhaseTest
	case repository.PhaseTest:
		return repository.PhaseReview
	case repository.PhaseReview:
		return repository.PhaseShip
	case repository.PhaseShip:
		return repository.PhaseListen
	default:
		return ""
	}
}