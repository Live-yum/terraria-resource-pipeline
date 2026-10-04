package xnb

import "encoding/binary"

// DXT color/alpha interpolation follows FNA's DtxUtil (see NOTICE).
func decodeDXT(src []byte, w, h, format int) []byte {
	dst := make([]byte, w*h*4)
	blockSize := 16
	if format == 4 {
		blockSize = 8
	}
	blocksX := (w + 3) / 4
	for by := 0; by < (h+3)/4; by++ {
		for bx := 0; bx < blocksX; bx++ {
			block := src[(by*blocksX+bx)*blockSize:][:blockSize]
			color := block
			if format != 4 {
				color = block[8:]
			}
			c0, c1 := binary.LittleEndian.Uint16(color), binary.LittleEndian.Uint16(color[2:])
			palette := [4][4]byte{}
			palette[0] = rgb565(c0)
			palette[1] = rgb565(c1)
			if format == 4 && c0 <= c1 {
				for ch := 0; ch < 3; ch++ {
					palette[2][ch] = byte((int(palette[0][ch]) + int(palette[1][ch])) / 2)
				}
				palette[2][3] = 255
			} else {
				for ch := 0; ch < 3; ch++ {
					palette[2][ch] = byte((2*int(palette[0][ch]) + int(palette[1][ch])) / 3)
					palette[3][ch] = byte((int(palette[0][ch]) + 2*int(palette[1][ch])) / 3)
				}
				palette[2][3] = 255
				palette[3][3] = 255
			}
			indices := binary.LittleEndian.Uint32(color[4:])
			var alphaMask uint64
			if format == 6 {
				for i := 0; i < 6; i++ {
					alphaMask |= uint64(block[2+i]) << (8 * i)
				}
			}
			for y := 0; y < 4; y++ {
				for x := 0; x < 4; x++ {
					px, py := bx*4+x, by*4+y
					if px >= w || py >= h {
						continue
					}
					i := y*4 + x
					p := palette[(indices>>(2*i))&3]
					a := p[3]
					switch format {
					case 5:
						nibble := (block[i/2] >> uint(4*(i%2))) & 15
						a = nibble * 17
					case 6:
						ai := (alphaMask >> uint(3*i)) & 7
						a = alphaDXT5(block[0], block[1], int(ai))
					}
					o := (py*w + px) * 4
					copy(dst[o:o+3], p[:3])
					dst[o+3] = a
				}
			}
		}
	}
	return dst
}

func rgb565(v uint16) [4]byte {
	r := int(v >> 11)
	g := int((v >> 5) & 63)
	b := int(v & 31)
	r = r*255 + 16
	g = g*255 + 32
	b = b*255 + 16
	return [4]byte{byte((r/32 + r) / 32), byte((g/64 + g) / 64), byte((b/32 + b) / 32), 255}
}

func alphaDXT5(a0, a1 byte, i int) byte {
	if i == 0 {
		return a0
	}
	if i == 1 {
		return a1
	}
	if a0 > a1 {
		return byte(((8-i)*int(a0) + (i-1)*int(a1)) / 7)
	}
	if i == 6 {
		return 0
	}
	if i == 7 {
		return 255
	}
	return byte(((6-i)*int(a0) + (i-1)*int(a1)) / 5)
}
