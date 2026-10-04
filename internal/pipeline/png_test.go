package pipeline

import (
	"bytes"
	"image"
	"image/color"
	"image/png"
	"testing"
)

func TestEncodeTexturePNGExactPixelsAndChoice(t *testing.T) {
	indexed := image.NewNRGBA(image.Rect(0, 0, 128, 128))
	colors := [3]color.NRGBA{
		{R: 17, G: 29, B: 43, A: 0}, // Hidden RGB is significant.
		{R: 17, G: 29, B: 43, A: 1},
		{R: 220, G: 61, B: 8, A: 255},
	}
	for y := 0; y < 128; y++ {
		for x := 0; x < 128; x++ {
			indexed.SetNRGBA(x, y, colors[(x/8+y/8)%len(colors)])
		}
	}
	tooMany := image.NewNRGBA(image.Rect(0, 0, 17, 16))
	for n := 0; n < 257; n++ {
		tooMany.SetNRGBA(n%17, n/17, color.NRGBA{R: uint8(n), G: uint8(n >> 8), B: 7, A: 255})
	}
	parent := image.NewNRGBA(image.Rect(0, 0, 32, 32))
	for y := 4; y < 20; y++ {
		for x := 5; x < 21; x++ {
			parent.SetNRGBA(x, y, colors[(x+y)%len(colors)])
		}
	}
	for _, test := range []struct {
		name        string
		img         *image.NRGBA
		wantIndexed bool
	}{
		{"alpha-and-first-appearance", indexed, true},
		{"over-256-colors", tooMany, false},
		{"subimage-offset", parent.SubImage(image.Rect(5, 4, 21, 20)).(*image.NRGBA), false},
	} {
		t.Run(test.name, func(t *testing.T) {
			var got, repeat, baseline bytes.Buffer
			if err := encodeTexturePNG(&got, test.img); err != nil {
				t.Fatal(err)
			}
			if err := encodeTexturePNG(&repeat, test.img); err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(got.Bytes(), repeat.Bytes()) {
				t.Fatal("PNG bytes are not deterministic")
			}
			if err := (&png.Encoder{CompressionLevel: png.DefaultCompression}).Encode(&baseline, test.img); err != nil {
				t.Fatal(err)
			}
			if got.Len() > baseline.Len() {
				t.Fatalf("output grew from %d to %d bytes", baseline.Len(), got.Len())
			}
			if (got.Bytes()[25] == 3) != test.wantIndexed {
				t.Fatalf("unexpected PNG color type %d", got.Bytes()[25])
			}
			decoded, err := png.Decode(bytes.NewReader(got.Bytes()))
			if err != nil {
				t.Fatal(err)
			}
			b := test.img.Bounds()
			for y := 0; y < b.Dy(); y++ {
				for x := 0; x < b.Dx(); x++ {
					want := test.img.NRGBAAt(b.Min.X+x, b.Min.Y+y)
					actual := color.NRGBAModel.Convert(decoded.At(x, y)).(color.NRGBA)
					if actual != want {
						t.Fatalf("pixel (%d,%d): got %+v, want %+v", x, y, actual, want)
					}
				}
			}
		})
	}
}
