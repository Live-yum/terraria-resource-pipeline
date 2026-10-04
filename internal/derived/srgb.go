package derived

import (
	"bufio"
	"encoding/binary"
	"fmt"
	"io"
	"os"
	"sort"
)

// BuildSRGB writes an uncompressed SRGB index. The caller handles gzip/hash.
// scratchDir must be an existing private job directory on disk, not system tmp.
// Temporary files are removed on success or error.
// Run indexes refer to StableCandidates(candidates), in that compact order.
func BuildSRGB(candidates []Candidate, writer io.Writer, scratchDir string) error {
	if err := checkCandidates(candidates); err != nil {
		return err
	}
	stable := StableCandidates(candidates)
	if len(stable) == 0 {
		return errNoStable
	}
	if writer == nil {
		return fmt.Errorf("derived: nil writer")
	}
	if scratchDir == "" {
		return fmt.Errorf("derived: private scratch directory is required")
	}
	cube, err := os.CreateTemp(scratchDir, "srgb-cube-*.bin")
	if err != nil {
		return err
	}
	defer os.Remove(cube.Name())
	defer cube.Close()
	records, err := os.CreateTemp(scratchDir, "srgb-runs-*.bin")
	if err != nil {
		return err
	}
	defer os.Remove(records.Name())
	defer records.Close()
	dist, label := make([]int32, cubeSize), make([]int32, cubeSize)
	var orders [2][]int
	for pass := 0; pass < 2; pass++ {
		order := make([]int, len(stable))
		for i := range order {
			order[i] = i
		}
		sort.SliceStable(order, func(i, j int) bool {
			a, b := stable[order[i]], stable[order[j]]
			if (a.Paint == 0) != (b.Paint == 0) {
				return a.Paint == 0
			}
			if pass == 1 && a.Kind != b.Kind {
				return a.Kind == 1
			}
			return order[i] < order[j]
		})
		orders[pass] = order
		sites := make([]site, len(order))
		ranks := make([]int32, len(order))
		for rank, original := range order {
			sites[rank] = site{rgb(stable[original]), int32(rank)}
			ranks[rank] = int32(rank)
		}
		exactCube(sites, ranks, dist, label)
		if pass == 0 {
			buf := bufio.NewWriterSize(cube, 1<<20)
			var line [512]byte
			for at := 0; at < cubeSize; at += 256 {
				for b := 0; b < 256; b++ {
					binary.LittleEndian.PutUint16(line[b*2:], uint16(order[label[at+b]]))
				}
				if _, err := buf.Write(line[:]); err != nil {
					return err
				}
			}
			if err := buf.Flush(); err != nil {
				return err
			}
			if _, err := cube.Seek(0, io.SeekStart); err != nil {
				return err
			}
		}
	}
	order := orders[1]
	directory := make([]uint32, 65537)
	buf := bufio.NewWriterSize(records, 1<<20)
	var first [512]byte
	var rec [5]byte
	for line := 0; line < 65536; line++ {
		if _, err := io.ReadFull(cube, first[:]); err != nil {
			return err
		}
		at := line * 256
		for b := 0; b < 256; b++ {
			a := binary.LittleEndian.Uint16(first[b*2:])
			w := uint16(order[label[at+b]])
			end := b == 255
			if !end {
				end = a != binary.LittleEndian.Uint16(first[(b+1)*2:]) || w != uint16(order[label[at+b+1]])
			}
			if end {
				rec[0] = byte(b)
				binary.LittleEndian.PutUint16(rec[1:], a)
				binary.LittleEndian.PutUint16(rec[3:], w)
				if _, err := buf.Write(rec[:]); err != nil {
					return err
				}
				directory[line+1]++
			}
		}
		directory[line+1] += directory[line]
	}
	if err := buf.Flush(); err != nil {
		return err
	}
	var header [12]byte
	copy(header[:], "SRGB")
	binary.LittleEndian.PutUint32(header[4:], uint32(len(stable)))
	binary.LittleEndian.PutUint32(header[8:], directory[65536])
	if err := writeAll(writer, header[:]); err != nil {
		return err
	}
	var dirBytes [65537 * 4]byte
	for i, v := range directory {
		binary.LittleEndian.PutUint32(dirBytes[i*4:], v)
	}
	if err := writeAll(writer, dirBytes[:]); err != nil {
		return err
	}
	if _, err := records.Seek(0, io.SeekStart); err != nil {
		return err
	}
	_, err = io.Copy(writer, records)
	return err
}
