package derived

import (
    "io"
    "testing"
)

func TestCompactCubeExactAgainstPriorInt32Transform(t *testing.T) {
    w := new(CubeWorkspace)
    dist, labels := w.acquire()
    oldDist, oldLabels := make([]int32, cubeSize), make([]int32, cubeSize)
    // Duplicate RGB seeds, exact midpoints, distant vertices and high labels.
    ranks := make([]int32, maxCandidates)
    for i := range ranks { ranks[i] = int32(len(ranks)-i) }
    sites := []site{{0, 0}, {2, 1}, {0x020000, 2}, {0x000200, 3},
        {0xffffff, 4}, {0x7f7f7f, 5}, {0x7f7f7f, maxCandidates-1}}
    exactCube(sites, ranks, dist, labels)
    legacyExactCube(sites, ranks, oldDist, oldLabels)
    for i := range dist {
        if dist[i] != oldDist[i] || int32(labels[i]) != oldLabels[i] {
            t.Fatalf("RGB %06x: compact (%d,%d) != prior (%d,%d)", i, dist[i], labels[i], oldDist[i], oldLabels[i])
        }
    }
    if w.Bytes() != 96<<20 { t.Fatalf("workspace bytes %d", w.Bytes()) }
    beforeDist, beforeLabel := &dist[0], &labels[0]
    nextDist, nextLabel := w.acquire()
    if beforeDist != &nextDist[0] || beforeLabel != &nextLabel[0] { t.Fatal("workspace not reused") }
    w.Release()
    if w.Bytes() != 0 { t.Fatal("workspace retained after release") }
}

func TestWorkspaceSerialBuildsAndPhaseAccounting(t *testing.T) {
    phases := map[string]bool{}
    w := &CubeWorkspace{Phase: func(name string, size uint64) {
        phases[name] = true
        if size != CubeWorkspaceBytes { t.Fatalf("phase %s size %d", name, size) }
    }}
    defer w.Release()
    candidates := []Candidate{{Stable: true}, {Kind: 1, B: 2, Stable: true}}
    if err := w.BuildSRGB(candidates, io.Discard, t.TempDir()); err != nil { t.Fatal(err) }
    before := &w.dist[0]
    if err := w.BuildTXCI(candidates, io.Discard, t.TempDir()); err != nil { t.Fatal(err) }
    if before != &w.dist[0] { t.Fatal("TXCI did not reuse SRGB cube") }
    for _, name := range []string{"srgb-transform", "srgb-spool", "srgb-runs", "srgb-output", "txci-transform", "txci-bricks", "txci-output"} {
        if !phases[name] { t.Fatalf("missing phase %s", name) }
    }
}
