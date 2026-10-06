package derived

// CubeWorkspace is an explicitly owned, serial workspace for SRGB and TXCI.
// It is never pooled globally: Release returns references after the final build.
// Both SRGB passes reuse the same buffers; TXCI can reuse them in a later stage.
// No change is made to 24-bit input precision or the integer tie-breaking ranks.
type CubeWorkspace struct {
    dist []int32
    label []uint16
    // Phase is optional and synchronous. It must not reenter this workspace.
    Phase func(name string, workspaceBytes uint64)
}

const CubeWorkspaceBytes uint64 = cubeSize * (4 + 2)

func (w *CubeWorkspace) acquire() ([]int32, []uint16) {
    if w.dist == nil { w.dist = make([]int32, cubeSize) }
    if w.label == nil { w.label = make([]uint16, cubeSize) }
    w.phase("cube-acquired")
    return w.dist, w.label
}

func (w *CubeWorkspace) phase(name string) {
    if w.Phase != nil { w.Phase(name, w.Bytes()) }
}

func (w *CubeWorkspace) Bytes() uint64 {
    return uint64(cap(w.dist))*4 + uint64(cap(w.label))*2
}

func (w *CubeWorkspace) Release() { w.dist = nil; w.label = nil }
