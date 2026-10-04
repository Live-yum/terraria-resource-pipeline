// Package derived builds exact 24-bit RGB nearest-color indexes.
package derived

import (
	"errors"
	"fmt"
)

// Candidate order is the caller's order. Kind is 0 for tile and 1 for wall.
// Stable selects SRGB candidates; TXCI includes every candidate.
type Candidate struct {
	Kind    uint16
	Type    uint16
	Variant uint16
	Paint   uint8
	R, G, B uint8
	Stable  bool
}

const cubeSize = 1 << 24
const maxCandidates = 65534

func checkCandidates(c []Candidate) error {
	if len(c) == 0 || len(c) > maxCandidates {
		return fmt.Errorf("derived: candidate count %d outside 1..%d", len(c), maxCandidates)
	}
	for i, v := range c {
		if v.Kind > 1 || v.Type > 0x7fff {
			return fmt.Errorf("derived: candidate %d has invalid kind/type", i)
		}
	}
	return nil
}

func rgb(c Candidate) uint32    { return uint32(c.R)<<16 | uint32(c.G)<<8 | uint32(c.B) }
func bgrRank(code uint32) int32 { return int32((code&255)<<16 | (code & 0xff00) | (code >> 16)) }

var errNoStable = errors.New("derived: no stable candidates")

// StableCandidates returns the SRGB candidate table in index order. Callers
// must publish this same table alongside BuildSRGB's binary index.
func StableCandidates(candidates []Candidate) []Candidate {
	stable := make([]Candidate, 0, len(candidates))
	for _, c := range candidates {
		if c.Stable {
			stable = append(stable, c)
		}
	}
	return stable
}
