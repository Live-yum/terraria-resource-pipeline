package derived

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/binary"
	"encoding/csv"
	"encoding/json"
	"io"
	"math/rand"
	"os"
	"strconv"
	"testing"
	"time"
)

func srgbLookup(raw []byte, code uint32, wall bool) uint16 {
	line := int(code >> 8)
	blue := byte(code)
	low := binary.LittleEndian.Uint32(raw[12+line*4:])
	high := binary.LittleEndian.Uint32(raw[16+line*4:])
	base := 12 + 65537*4
	for low < high {
		mid := (low + high) / 2
		if raw[base+int(mid)*5] < blue {
			low = mid + 1
		} else {
			high = mid
		}
	}
	at := base + int(low)*5 + 1
	if wall {
		at += 2
	}
	return binary.LittleEndian.Uint16(raw[at:])
}

func txciLookup(raw []byte, code uint32) uint16 {
	bs := int(binary.LittleEndian.Uint16(raw[6:]))
	grid := 256 / bs
	r, g, b := int(code>>16), int((code>>8)&255), int(code&255)
	id := ((r/bs)*grid+g/bs)*grid + b/bs
	local := ((r%bs)*bs+g%bs)*bs + b%bs
	dir := int(binary.LittleEndian.Uint32(raw[32:]))
	payload := int(binary.LittleEndian.Uint32(raw[36:]))
	entry := raw[dir+id*8:]
	p := raw[payload+int(binary.LittleEndian.Uint32(entry[4:])):]
	switch entry[0] {
	case 0:
		return binary.LittleEndian.Uint16(p)
	case 1:
		k := int(p[0])
		nib := p[1+k*2+local/2]
		idx := int(nib & 15)
		if local&1 == 1 {
			idx = int(nib >> 4)
		}
		return binary.LittleEndian.Uint16(p[1+idx*2:])
	case 2:
		k := int(binary.LittleEndian.Uint16(p))
		idx := int(p[2+k*2+local])
		return binary.LittleEndian.Uint16(p[2+idx*2:])
	case 3:
		return binary.LittleEndian.Uint16(p[local*2:])
	}
	panic("invalid TXCI block")
}

func TestSynthetic(t *testing.T) {
	c := []Candidate{
		{Kind: 0, Type: 2, B: 0, Stable: true},
		{Kind: 0, Type: 3, B: 1, Stable: false},
		{Kind: 1, Type: 1, B: 2, Stable: true},
	}
	var s bytes.Buffer
	if err := BuildSRGB(c, &s); err != nil {
		t.Fatal(err)
	}
	if got := srgbLookup(s.Bytes(), 1, false); got != 0 {
		t.Fatalf("SRGB tie=%d", got)
	}
	if got := srgbLookup(s.Bytes(), 1, true); got != 1 {
		t.Fatalf("SRGB wall tie=%d", got)
	}
	if got := srgbLookup(s.Bytes(), 2, false); got != 1 {
		t.Fatalf("SRGB exact=%d", got)
	}
	if got := binary.LittleEndian.Uint32(s.Bytes()[4:]); got != 2 {
		t.Fatalf("candidate count=%d", got)
	}
	if stable := StableCandidates(c); len(stable) != 2 || stable[0].Type != 2 || stable[1].Type != 1 {
		t.Fatalf("stable table=%#v", stable)
	}
	var x bytes.Buffer
	if err := BuildTXCI(c, &x); err != nil {
		t.Fatal(err)
	}
	if got := txciLookup(x.Bytes(), 1); got != 1 {
		t.Fatalf("TXCI exact group=%d", got)
	}
	if got := txciLookup(x.Bytes(), 0); got != 0 {
		t.Fatalf("TXCI origin group=%d", got)
	}
	if got := binary.LittleEndian.Uint32(x.Bytes()[8:]); got != 3 {
		t.Fatalf("TXCI group count=%d", got)
	}
	if got := binary.LittleEndian.Uint32(x.Bytes()[16:]); got != brickCount {
		t.Fatalf("TXCI brick count=%d", got)
	}
}

func TestCandidateLimits(t *testing.T) {
	if err := BuildSRGB([]Candidate{{}}, io.Discard); err == nil {
		t.Fatal("accepted no stable candidates")
	}
	if err := BuildTXCI([]Candidate{{Kind: 2}}, io.Discard); err == nil {
		t.Fatal("accepted invalid kind")
	}
	if err := BuildTXCI(make([]Candidate, maxCandidates+1), io.Discard); err == nil {
		t.Fatal("accepted too many candidates")
	}
}

func TestLargeRGBGroup(t *testing.T) {
	c := make([]Candidate, 300)
	for i := range c {
		c[i] = Candidate{Type: uint16(i), Variant: uint16(i + 300), Paint: uint8(i % 31), R: 3, G: 4, B: 5}
	}
	var out bytes.Buffer
	if err := BuildTXCI(c, &out); err != nil {
		t.Fatal(err)
	}
	b := out.Bytes()
	if got := binary.LittleEndian.Uint32(b[8:]); got != 1 {
		t.Fatalf("group count=%d", got)
	}
	groupsOff := int(binary.LittleEndian.Uint32(b[24:]))
	itemsOff := int(binary.LittleEndian.Uint32(b[28:]))
	if got := binary.LittleEndian.Uint32(b[groupsOff+4:]); got != 300 {
		t.Fatalf("group option count=%d", got)
	}
	last := b[itemsOff+299*6:]
	if got := binary.LittleEndian.Uint16(last[2:]); got != 599 {
		t.Fatalf("variant was truncated: %d", got)
	}
}

type countWriter int64

func (w *countWriter) Write(p []byte) (int, error) { *w += countWriter(len(p)); return len(p), nil }

func TestRuntimeCandidates(t *testing.T) {
	path := os.Getenv("DERIVED_RUNTIME_CANDIDATES")
	if path == "" {
		t.Skip("set DERIVED_RUNTIME_CANDIDATES")
	}
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	dec := json.NewDecoder(f)
	var all []Candidate
	groups := make(map[uint32]int)
	maxGroup, maxVariant, stable := 0, 0, 0
	for {
		var row struct {
			Kind    string
			Type    uint16
			Variant uint16
			Paint   uint8
			Color   struct{ R, G, B uint8 }
			Stable  bool
		}
		err := dec.Decode(&row)
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		kind := uint16(0)
		if row.Kind == "wall" {
			kind = 1
		} else if row.Kind != "tile" {
			t.Fatalf("invalid kind %q", row.Kind)
		}
		c := Candidate{Kind: kind, Type: row.Type, Variant: row.Variant, Paint: row.Paint, R: row.Color.R, G: row.Color.G, B: row.Color.B, Stable: row.Stable}
		all = append(all, c)
		groups[rgb(c)]++
		maxGroup = max(maxGroup, groups[rgb(c)])
		maxVariant = max(maxVariant, int(c.Variant))
		if c.Stable {
			stable++
		}
	}
	t.Logf("runtime candidates=%d stable=%d unique RGB=%d largest group=%d max variant=%d", len(all), stable, len(groups), maxGroup, maxVariant)
	var txci, srgb countWriter
	start := time.Now()
	if err := BuildTXCI(all, &txci); err != nil {
		t.Fatal(err)
	}
	txciTime := time.Since(start)
	start = time.Now()
	if err := BuildSRGB(all, &srgb); err != nil {
		t.Fatal(err)
	}
	t.Logf("TXCI bytes=%d duration=%s; SRGB bytes=%d duration=%s", txci, txciTime, srgb, time.Since(start))
}

// These optional oracle tests consume existing artifacts only as fixtures.
// Production generation never reads them.
func TestRealSRGBOracle(t *testing.T) {
	path, oracle := os.Getenv("DERIVED_SRGB_CANDIDATES"), os.Getenv("DERIVED_SRGB_ORACLE")
	if path == "" || oracle == "" {
		t.Skip("set DERIVED_SRGB_CANDIDATES and DERIVED_SRGB_ORACLE")
	}
	f, e := os.Open(path)
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	c := make([]Candidate, len(rows))
	for i, row := range rows {
		if len(row) != 4 {
			t.Fatalf("row %d", i)
		}
		kind, _ := strconv.Atoi(row[0])
		typ, _ := strconv.Atoi(row[1])
		paint, _ := strconv.Atoi(row[2])
		code, _ := strconv.ParseUint(row[3], 10, 24)
		c[i] = Candidate{Kind: uint16(kind), Type: uint16(typ), Paint: uint8(paint), R: uint8(code >> 16), G: uint8(code >> 8), B: uint8(code), Stable: true}
	}
	var actual bytes.Buffer
	if e := BuildSRGB(c, &actual); e != nil {
		t.Fatal(e)
	}
	file, e := os.Open(oracle)
	if e != nil {
		t.Fatal(e)
	}
	defer file.Close()
	z, e := gzip.NewReader(file)
	if e != nil {
		t.Fatal(e)
	}
	defer z.Close()
	expected, e := io.ReadAll(z)
	if e != nil {
		t.Fatal(e)
	}
	if len(expected) != actual.Len() {
		t.Fatalf("SRGB bytes %d, oracle %d", actual.Len(), len(expected))
	}
	rng := rand.New(rand.NewSource(1458))
	for _, code := range []uint32{0, 1, 0xff, 0x100, 0x7f7f7f, 0xfffffe, 0xffffff} {
		for _, wall := range []bool{false, true} {
			if a, b := srgbLookup(actual.Bytes(), code, wall), srgbLookup(expected, code, wall); a != b {
				t.Fatalf("RGB %06x wall=%v got %d oracle %d", code, wall, a, b)
			}
		}
	}
	for i := 0; i < 10000; i++ {
		code := uint32(rng.Intn(cubeSize))
		for _, wall := range []bool{false, true} {
			if a, b := srgbLookup(actual.Bytes(), code, wall), srgbLookup(expected, code, wall); a != b {
				t.Fatalf("random RGB %06x wall=%v got %d oracle %d", code, wall, a, b)
			}
		}
	}
	t.Logf("SRGB oracle sample 20014 lookups matched; raw hash %x, oracle %x", sha256.Sum256(actual.Bytes()), sha256.Sum256(expected))
}

func TestRealTXCIOracle(t *testing.T) {
	path := os.Getenv("DERIVED_TXCI_ORACLE")
	if path == "" {
		t.Skip("set DERIVED_TXCI_ORACLE")
	}
	expected, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	if string(expected[:4]) != "TXCI" {
		t.Fatal("oracle magic")
	}
	n := int(binary.LittleEndian.Uint32(expected[8:]))
	colorsOff := int(binary.LittleEndian.Uint32(expected[20:]))
	groupsOff := int(binary.LittleEndian.Uint32(expected[24:]))
	itemsOff := int(binary.LittleEndian.Uint32(expected[28:]))
	c := make([]Candidate, 0, int(binary.LittleEndian.Uint32(expected[12:])))
	for id := 0; id < n; id++ {
		code := expected[colorsOff+id*3:]
		start := int(binary.LittleEndian.Uint32(expected[groupsOff+id*4:]))
		end := int(binary.LittleEndian.Uint32(expected[groupsOff+(id+1)*4:]))
		for i := start; i < end; i++ {
			item := expected[itemsOff+i*6:]
			packed := binary.LittleEndian.Uint16(item)
			kind := uint16(0)
			if packed&0x8000 != 0 {
				kind = 1
			}
			c = append(c, Candidate{Kind: kind, Type: packed & 0x7fff, Variant: binary.LittleEndian.Uint16(item[2:]), Paint: item[4], R: code[0], G: code[1], B: code[2]})
		}
	}
	var actual bytes.Buffer
	if e := BuildTXCI(c, &actual); e != nil {
		t.Fatal(e)
	}
	rng := rand.New(rand.NewSource(1458))
	for _, code := range []uint32{0, 1, 0xff, 0x100, 0x7f7f7f, 0xfffffe, 0xffffff} {
		if a, b := txciLookup(actual.Bytes(), code), txciLookup(expected, code); a != b {
			t.Fatalf("RGB %06x got group %d oracle %d", code, a, b)
		}
	}
	for i := 0; i < 10000; i++ {
		code := uint32(rng.Intn(cubeSize))
		if a, b := txciLookup(actual.Bytes(), code), txciLookup(expected, code); a != b {
			t.Fatalf("random RGB %06x got group %d oracle %d", code, a, b)
		}
	}
	t.Logf("TXCI oracle sample 10007 lookups matched; bytes %d oracle %d; hash %x oracle %x", actual.Len(), len(expected), sha256.Sum256(actual.Bytes()), sha256.Sum256(expected))
	if actual.Len() != len(expected) {
		t.Logf("TXCI layout differs by %d bytes", actual.Len()-len(expected))
	}
}
