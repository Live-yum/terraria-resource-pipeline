package derived

import (
	"bufio"
	"encoding/binary"
	"fmt"
	"io"
	"os"
	"sort"
)

const brickSize = 8
const brickCount = 32 * 32 * 32

// BuildTXCI writes a raw TXCI v3 index with 8x8x8 bricks. The caller handles
// gzip/hash. Equal-distance groups use the old SciPy EDT's B,G,R coordinate
// tie order; items within one RGB group use the original priority_key order.
// scratchDir must be an existing private job directory on disk, not system tmp.
// Temporary files are removed on success or error.
func BuildTXCI(candidates []Candidate, writer io.Writer, scratchDir string) error {
	return new(CubeWorkspace).BuildTXCI(candidates, writer, scratchDir)
}

// BuildTXCI reuses the workspace buffers; calls on one workspace must be serial.
func (workspace *CubeWorkspace) BuildTXCI(candidates []Candidate, writer io.Writer, scratchDir string) error {
	if err := checkCandidates(candidates); err != nil {
		return err
	}
	if writer == nil {
		return fmt.Errorf("derived: nil writer")
	}
	if scratchDir == "" {
		return fmt.Errorf("derived: private scratch directory is required")
	}
	groups := make(map[uint32][]int, len(candidates))
	for i, c := range candidates {
		code := rgb(c)
		groups[code] = append(groups[code], i)
	}
	colors := make([]uint32, 0, len(groups))
	for code := range groups {
		colors = append(colors, code)
	}
	sort.Slice(colors, func(i, j int) bool { return colors[i] < colors[j] })
	if len(colors) > 65535 {
		return fmt.Errorf("derived: too many TXCI RGB groups")
	}
	groupOffsets := make([]uint32, len(colors)+1)
	items := make([]byte, 0, len(candidates)*6)
	sites := make([]site, len(colors))
	ranks := make([]int32, len(colors))
	for id, code := range colors {
		group := groups[code]
		sort.SliceStable(group, func(i, j int) bool {
			a, b := candidates[group[i]], candidates[group[j]]
			if a.Kind != b.Kind {
				return a.Kind < b.Kind
			}
			if (a.Paint == 0) != (b.Paint == 0) {
				return a.Paint == 0
			}
			if a.Type != b.Type {
				return a.Type < b.Type
			}
			if a.Variant != b.Variant {
				return a.Variant < b.Variant
			}
			if a.Paint != b.Paint {
				return a.Paint < b.Paint
			}
			return group[i] < group[j]
		})
		for _, index := range group {
			c := candidates[index]
			kindType := c.Type
			if c.Kind == 1 {
				kindType |= 0x8000
			}
			items = append(items, byte(kindType), byte(kindType>>8), byte(c.Variant), byte(c.Variant>>8), c.Paint, 0)
		}
		groupOffsets[id+1] = uint32(len(items) / 6)
		sites[id] = site{code, int32(id)}
		ranks[id] = bgrRank(code)
	}
	dist, label := workspace.acquire()
	workspace.phase("txci-transform")
	exactCube(sites, ranks, dist, label)
	workspace.phase("txci-bricks")
	payload, err := os.CreateTemp(scratchDir, "txci-payload-*.bin")
	if err != nil {
		return err
	}
	defer os.Remove(payload.Name())
	defer payload.Close()
	buffer := bufio.NewWriterSize(payload, 1<<20)
	directory := make([]byte, brickCount*8)
	seen := make([]uint32, len(colors))
	paletteIndex := make([]uint16, len(colors))
	var block [512]uint16
	var unique [512]uint16
	var encoded [1026]byte
	var payloadAt uint32
	brickID := 0
	for r0 := 0; r0 < 256; r0 += brickSize {
		for g0 := 0; g0 < 256; g0 += brickSize {
			for b0 := 0; b0 < 256; b0 += brickSize {
				n := 0
				epoch := uint32(brickID + 1)
				for r := 0; r < 8; r++ {
					for g := 0; g < 8; g++ {
						for b := 0; b < 8; b++ {
							id := uint16(label[((r0+r)<<16)|((g0+g)<<8)|(b0+b)])
							block[(r*8+g)*8+b] = id
							if seen[id] != epoch {
								seen[id] = epoch
								unique[n] = id
								n++
							}
						}
					}
				}
				palette := unique[:n]
				sort.Slice(palette, func(i, j int) bool { return palette[i] < palette[j] })
				for i, id := range palette {
					paletteIndex[id] = uint16(i)
				}
				typ, count, length := byte(0), byte(1), 0
				switch {
				case n == 1:
					binary.LittleEndian.PutUint16(encoded[:], palette[0])
					length = 2
				case n <= 16:
					typ, count = 1, byte(n)
					encoded[0] = byte(n)
					for i, id := range palette {
						binary.LittleEndian.PutUint16(encoded[1+i*2:], id)
					}
					base := 1 + n*2
					for i := 0; i < 512; i += 2 {
						encoded[base+i/2] = byte(paletteIndex[block[i]] | paletteIndex[block[i+1]]<<4)
					}
					length = base + 256
				case n <= 256:
					typ, count = 2, byte(n)
					binary.LittleEndian.PutUint16(encoded[:], uint16(n))
					for i, id := range palette {
						binary.LittleEndian.PutUint16(encoded[2+i*2:], id)
					}
					base := 2 + n*2
					for i, id := range block {
						encoded[base+i] = byte(paletteIndex[id])
					}
					length = base + 512
				default:
					typ, count = 3, 0
					for i, id := range block {
						binary.LittleEndian.PutUint16(encoded[i*2:], id)
					}
					length = 1024
				}
				entry := directory[brickID*8:][:8]
				entry[0], entry[1] = typ, count
				binary.LittleEndian.PutUint32(entry[4:], payloadAt)
				if _, err := buffer.Write(encoded[:length]); err != nil {
					return err
				}
				payloadAt += uint32(length)
				brickID++
			}
		}
	}
	if err := buffer.Flush(); err != nil {
		return err
	}
	if _, err := payload.Seek(0, io.SeekStart); err != nil {
		return err
	}
	workspace.phase("txci-output")
	colorsOff := uint32(44)
	groupsOff := align4(colorsOff + uint32(len(colors)*3))
	itemsOff := groupsOff + uint32(len(groupOffsets)*4)
	directoryOff := align4(itemsOff + uint32(len(items)))
	payloadOff := directoryOff + uint32(len(directory))
	var header [44]byte
	copy(header[:], "TXCI")
	binary.LittleEndian.PutUint16(header[4:], 3)
	binary.LittleEndian.PutUint16(header[6:], brickSize)
	for i, v := range [...]uint32{uint32(len(colors)), uint32(len(candidates)), brickCount, colorsOff, groupsOff, itemsOff, directoryOff, payloadOff, 0} {
		binary.LittleEndian.PutUint32(header[8+i*4:], v)
	}
	if err := writeAll(writer, header[:]); err != nil {
		return err
	}
	var color [3]byte
	for _, code := range colors {
		color = [3]byte{byte(code >> 16), byte(code >> 8), byte(code)}
		if err := writeAll(writer, color[:]); err != nil {
			return err
		}
	}
	if err := writePadding(writer, int(groupsOff-colorsOff-uint32(len(colors)*3))); err != nil {
		return err
	}
	var offset [4]byte
	for _, v := range groupOffsets {
		binary.LittleEndian.PutUint32(offset[:], v)
		if err := writeAll(writer, offset[:]); err != nil {
			return err
		}
	}
	if err := writeAll(writer, items); err != nil {
		return err
	}
	if err := writePadding(writer, int(directoryOff-itemsOff-uint32(len(items)))); err != nil {
		return err
	}
	if err := writeAll(writer, directory); err != nil {
		return err
	}
	_, err = io.Copy(writer, payload)
	return err
}

func align4(n uint32) uint32                { return (n + 3) &^ uint32(3) }
func writePadding(w io.Writer, n int) error { var zeros [3]byte; return writeAll(w, zeros[:n]) }
func writeAll(w io.Writer, b []byte) error {
	for len(b) > 0 {
		n, e := w.Write(b)
		if e != nil {
			return e
		}
		if n == 0 {
			return io.ErrShortWrite
		}
		b = b[n:]
	}
	return nil
}
