package xnb

import (
	"bytes"
	"encoding/binary"
	"errors"
	"image/color"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func xnbTexture(format, w, h uint32, pixels []byte) []byte {
	var body bytes.Buffer
	name := "Microsoft.Xna.Framework.Content.Texture2DReader"
	body.WriteByte(1)
	body.WriteByte(byte(len(name)))
	body.WriteString(name)
	binary.Write(&body, binary.LittleEndian, uint32(0))
	body.WriteByte(0)
	body.WriteByte(1)
	for _, n := range []uint32{format, w, h, 1, uint32(len(pixels))} {
		binary.Write(&body, binary.LittleEndian, n)
	}
	body.Write(pixels)
	out := append([]byte("XNBw\x05\x00"), make([]byte, 4)...)
	binary.LittleEndian.PutUint32(out[6:], uint32(len(out)+body.Len()))
	return append(out, body.Bytes()...)
}

func TestTexture(t *testing.T) {
	if got := rgb565(3 << 11); got != ([4]byte{25, 0, 0, 255}) {
		t.Fatalf("RGB565 rounding = %#v", got)
	}
	for _, tt := range []struct {
		name   string
		format uint32
		data   []byte
		want   color.NRGBA
	}{
		{"raw", 0, []byte{255, 80, 20, 30}, color.NRGBA{255, 80, 20, 30}},
		{"dxt1", 4, []byte{0x00, 0xf8, 0, 0, 0, 0, 0, 0}, color.NRGBA{255, 0, 0, 255}},
		{"dxt3", 5, []byte{0x08, 0, 0, 0, 0, 0, 0, 0, 0x00, 0xf8, 0, 0, 0, 0, 0, 0}, color.NRGBA{255, 0, 0, 136}},
		{"dxt5", 6, []byte{20, 10, 0, 0, 0, 0, 0, 0, 0x00, 0xf8, 0, 0, 0, 0, 0, 0}, color.NRGBA{255, 0, 0, 20}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			data := xnbTexture(tt.format, 1, 1, tt.data)
			im, err := Decode(bytes.NewReader(data), int64(len(data)))
			if err != nil {
				t.Fatal(err)
			}
			if got := im.NRGBAAt(0, 0); got != tt.want {
				t.Fatalf("pixel = %#v, want %#v", got, tt.want)
			}
			data[len(data)-1] ^= 1 // mip content can change, but declared size must still bind
			if _, err := Decode(bytes.NewReader(data), int64(len(data)-1)); err == nil {
				t.Fatal("accepted oversized input")
			}
		})
	}
}

func TestRejectMalformed(t *testing.T) {
	raw := xnbTexture(0, 1, 1, []byte{1, 2, 3, 4})
	for _, mutate := range []func([]byte){
		func(b []byte) { b[3] = '?' },
		func(b []byte) { binary.LittleEndian.PutUint32(b[6:], uint32(len(b)+1)) },
		func(b []byte) { binary.LittleEndian.PutUint32(b[len(b)-8:], 999) },
	} {
		b := bytes.Clone(raw)
		mutate(b)
		if _, e := Decode(bytes.NewReader(b), int64(len(b))); e == nil {
			t.Fatal("accepted malformed XNB")
		}
	}
	if _, e := decompressLZX([]byte{0xff, 0, 1, 0, 2, 0}, 1); e == nil {
		t.Fatal("accepted truncated LZX frame")
	}
	non := bytes.Clone(raw)
	name := []byte("Microsoft.Xna.Framework.Content.Texture2DReader")
	copy(non[12:12+len(name)], strings.Repeat("X", len(name)))
	if _, e := Decode(bytes.NewReader(non), int64(len(non))); !errors.Is(e, ErrNotTexture) {
		t.Fatalf("nontexture: %v", e)
	}
}

func TestLZXUncompressedFrame(t *testing.T) {
	raw := xnbTexture(0, 1, 1, []byte{7, 9, 11, 13})
	payload := raw[10:]
	frame := make([]byte, 16+len(payload))
	// Header bit 0, block type 3, then the 24-bit uncompressed length.
	header := uint32(3)<<28 | uint32(len(payload))<<4
	binary.LittleEndian.PutUint16(frame, uint16(header>>16))
	binary.LittleEndian.PutUint16(frame[2:], uint16(header))
	for i := 4; i < 16; i += 4 {
		binary.LittleEndian.PutUint32(frame[i:], 1)
	}
	copy(frame[16:], payload)
	out := append([]byte("XNBw\x05\x80"), make([]byte, 8)...)
	binary.LittleEndian.PutUint32(out[10:], uint32(len(payload)))
	out = append(out, 0xff, byte(len(payload)>>8), byte(len(payload)), byte(len(frame)>>8), byte(len(frame)))
	out = append(out, frame...)
	out = append(out, 0, 0, 0, 0, 0)
	binary.LittleEndian.PutUint32(out[6:], uint32(len(out)))
	im, err := Decode(bytes.NewReader(out), int64(len(out)))
	if err != nil {
		t.Fatal(err)
	}
	if got := im.NRGBAAt(0, 0); got != (color.NRGBA{7, 9, 11, 13}) {
		t.Fatalf("pixel = %#v", got)
	}
	out[len(out)-1] = 1
	if _, err := Decode(bytes.NewReader(out), int64(len(out))); err == nil {
		t.Fatal("accepted nonzero LZX trailer")
	}
}

func TestRealImages(t *testing.T) {
	root := os.Getenv("XNB_REAL_DIR")
	if root == "" {
		t.Skip("set XNB_REAL_DIR to scan real content")
	}
	count, notTexture, compressed := 0, 0, 0
	var peakHeap, peakSys uint64
	var firstError error
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, e error) error {
		if e != nil {
			return e
		}
		if d.IsDir() || !strings.EqualFold(filepath.Ext(path), ".xnb") {
			return nil
		}
		count++
		f, e := os.Open(path)
		if e != nil {
			return e
		}
		st, e := f.Stat()
		if e != nil {
			f.Close()
			return e
		}
		var header [6]byte
		if _, e := f.ReadAt(header[:], 0); e != nil {
			f.Close()
			return e
		}
		if header[5]&0x80 != 0 {
			compressed++
		}
		im, e := Decode(f, st.Size())
		f.Close()
		if errors.Is(e, ErrNotTexture) {
			notTexture++
		} else if e != nil && firstError == nil {
			firstError = errors.New(path + ": " + e.Error())
		} else if e == nil && (im.Rect.Dx() < 1 || im.Rect.Dy() < 1) {
			firstError = errors.New(path + ": empty image")
		}
		if count%256 == 0 {
			var m runtime.MemStats
			runtime.ReadMemStats(&m)
			peakHeap = max(peakHeap, m.HeapAlloc)
			peakSys = max(peakSys, m.Sys)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("XNB files=%d LZX=%d raw=%d textures=%d nontexture=%d max heap sample=%d MiB max Sys sample=%d MiB", count, compressed, count-compressed, count-notTexture, notTexture, peakHeap>>20, peakSys>>20)
	if firstError != nil {
		t.Fatal(firstError)
	}
	if count == 0 {
		t.Fatal(io.EOF)
	}
}
