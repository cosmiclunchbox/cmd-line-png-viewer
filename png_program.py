import struct, zlib
import sys
import math

PNG_HEADER = b'\x89PNG\x0d\x0a\x1a\x0a'
PNG_CHUNK_LEN_SIZE = 4
PNG_CHUNK_TYPE_SIZE = 4
PNG_CHUNK_CRC_SIZE = 4

CHUNK_TYPE_IHDR = 'IHDR'
CHUNK_TYPE_PLTE = 'PLTE'
CHUNK_TYPE_IDAT = 'IDAT'
CHUNK_TYPE_IEND = 'IEND'

IHDR_CHUNK_SIZE = 4 + 4 + 1 + 1 + 1 + 1 + 1

DEFAULT_PIXEL_TEXT = '██'
PIXEL_TEXT = DEFAULT_PIXEL_TEXT #'HI' #'╳╳'
BACKGROUND_IS_WHITE = False

DEBUG_PRINT = True

'''
Optionally prints out the given data. Used for debugging purposes.
'''
def debug_print(thing, end='\n'):
    if DEBUG_PRINT:
        sys.stderr.write(str(thing) + end)

'''
Reads the first 8 bytes of the given file and checks that it matches the PNG header given in the spec.
If it matches, do nothing. Otherwise, raise an error.
'''
def read_validate_png_header(f):
    if f.read(len(PNG_HEADER)) != PNG_HEADER:
        raise Exception('Invalid file header.')

'''
Converts the given bytes provided in big-endian order into an integer.
'''
def _bytes_to_int(bytes):
    return int.from_bytes(bytes, 'big')
    
'''
Starting from the current file offset, attempts to read a single PNG chunk. Returns the 4-byte chunk length,
the 4-byte chunk type, the chunk data, and the 4-byte checksum. Assumes that the file offset is located at the
start of a chunk. Does not explicitly detect any data corruption or other errors. Advances the file offset to
the end of the chunk that is being read.
'''
def read_chunk(f):
    chunk_len = _bytes_to_int(f.read(PNG_CHUNK_LEN_SIZE))
    chunk_type = f.read(PNG_CHUNK_TYPE_SIZE).decode()
    chunk_data = f.read(chunk_len)
    chunk_crc = f.read(PNG_CHUNK_CRC_SIZE)

    return chunk_len, chunk_type, chunk_data, chunk_crc

'''
Starting from the current file offset, attempts to read all the PNG chunks in the file until reaching an IEND
chunk. Assumes the file offset is located at the start of a chunk. Advances the file offset to the end of the
IEND chunk, i.e. the end of the PNG file.
'''
def read_chunks(f):
    chunks = []
    while True:
        _, chunk_type, chunk_data, _ = read_chunk(f)
        if chunk_type == CHUNK_TYPE_IEND:
            break
        chunks.append((chunk_type, chunk_data))
    return chunks


'''
Processes the given chunk (provided as a tuple of chunk_type, chunk_data) as an IHDR chunk. Returns all the
information found in the chunk data.
'''
def process_IHDR_chunk(chunk):
    chunk_type = chunk[0]
    chunk_data = chunk[1]
    chunk_len = len(chunk_data)
    assert chunk_type == CHUNK_TYPE_IHDR, f'Chunk type {chunk_type} is not IHDR.'
    assert chunk_len == IHDR_CHUNK_SIZE

    img_width = _bytes_to_int(chunk_data[:4])
    img_height = _bytes_to_int(chunk_data[4:8])
    bit_depth = _bytes_to_int(chunk_data[8:9])
    color_type = _bytes_to_int(chunk_data[9:10])
    compression_method = _bytes_to_int(chunk_data[10:11])
    filter_method = _bytes_to_int(chunk_data[11:12])
    interlace_method = _bytes_to_int(chunk_data[12:])

    assert img_width != 0
    assert img_height != 0

    return img_width, img_height, bit_depth, color_type, compression_method, filter_method, interlace_method

'''
Processes the given chunk (provided as a tuple of chunk_type, chunk_data) as a PLTE chunk. Returns the palette
of colors as a list of 3-tuples.
'''
def process_PLTE_chunk(chunk):
    chunk_type = chunk[0]
    chunk_data = chunk[1]
    chunk_len = len(chunk_data)
    assert chunk_type == CHUNK_TYPE_PLTE, f'Chunk type {chunk_type} is not PLTE.'

    palette = []
    for i in range(0, chunk_len, 3):
        palette.append([chunk_data[i], chunk_data[i + 1], chunk_data[i + 2]])

    return palette

'''
Concatenates the data fields of all the IDAT chunks in the given list and returns the resulting byte sequence.
'''
def coalesce_image_data(chunks):
    return b''.join([chunk[1] for chunk in chunks if chunk[0] == CHUNK_TYPE_IDAT])

'''
Uncompresses the given byte sequence obtained directly from the IDAT chunks.
'''
def uncompress_image_bytes(image_data):
    return zlib.decompress(image_data)

def _get_num_color_channels(color_type):
    return {
        0: 1,
        2: 3,
        3: 1,
        4: 2,
        6: 4
    }[color_type]

'''
Unfilters the raw uncompressed image data.
'''
def unfilter_image_data(raw_img_data, img_width, img_height, bit_depth, color_type, palette=None):
    allowed_combinations = {
        0: (1, 2, 4, 8, 16),
        2: (8, 16),
        3: (1, 2, 4, 8),
        4: (8, 16),
        6: (8, 16)
    }
    assert color_type in allowed_combinations
    assert bit_depth in allowed_combinations[color_type]
    if color_type == 3:
        assert palette

    num_channels = _get_num_color_channels(color_type)
    
    unfiltered_img_data = []
    bytes_per_pixel = int(bit_depth * num_channels / 8)
    row_width = math.ceil(img_width * bit_depth * num_channels / 8)

    def recon_a(x, y):
        if x < bytes_per_pixel:
            return 0
        else:
            return unfiltered_img_data[y * row_width + x - max(bytes_per_pixel, 1)]
        
    def recon_b(x, y):
        if y <= 0:
            return 0
        else:
            return unfiltered_img_data[(y - 1) * row_width + x]
        
    def recon_c(x, y):
        if x < bytes_per_pixel or y <= 0:
            return 0
        else:
            return unfiltered_img_data[(y - 1) * row_width + x - max(bytes_per_pixel, 1)] 
        
    # copy-pasted this straight from https://pyokagan.name/blog/2019-10-14-png/
    def PaethPredictor(a, b, c):
        p = a + b - c
        pa = abs(p - a)
        pb = abs(p - b)
        pc = abs(p - c)
        if pa <= pb and pa <= pc:
            Pr = a
        elif pb <= pc:
            Pr = b
        else:
            Pr = c
        return Pr
    
    filter_values = {
        0: lambda x, y: 0,
        1: lambda x, y: recon_a(x, y),
        2: lambda x, y: recon_b(x, y),
        3: lambda x, y: (recon_a(x, y) + recon_b(x, y)) // 2,
        4: lambda x, y: PaethPredictor(recon_a(x, y), recon_b(x, y), recon_c(x, y))
    }

    filter_types = []
    for line_index in range(img_height):
        filter_types.append(raw_img_data[line_index * (1 + row_width)])

    i = 0
    for y in range(img_height):
        filter_type = filter_types[y]
        i += 1
        for x in range(img_width):
            for n in range(num_channels):
                unfiltered_img_data.append((raw_img_data[i] + filter_values[filter_type](x * num_channels + n, y)) % 256)
                i += 1
    
    return unfiltered_img_data

def _color_type_0_to_RGBA(img_data, bit_index, bit_depth):
    cur_byte = img_data[bit_index // 8]
    color_value = (cur_byte >> ((-bit_index - bit_depth) % 8)) & (2 ** bit_index - 1)
    color_tuple = [color_value * int(256 / 2 ** bit_depth)] * 3
    color_tuple.append(255)
    return color_tuple

def _color_type_2_to_RGBA(img_data, bit_index, bit_depth):
    color_tuple = []
    for n in range(_get_num_color_channels(2)):
        cur_byte = img_data[bit_index // 8]
        color_tuple.append(cur_byte)
        bit_index += bit_depth
    color_tuple.append(255)
    return color_tuple

def _color_type_3_to_RGBA(img_data, bit_index, bit_depth, palette):
    cur_byte = img_data[bit_index // 8]
    color_value = (cur_byte >> ((-bit_index - bit_depth) % 8)) & (2 ** bit_index - 1)
    color_tuple = palette[color_value][:]
    color_tuple.append(255)
    return color_tuple

def _color_type_4_to_RGBA(img_data, bit_index, bit_depth):
    cur_byte = img_data[bit_index // 8]
    color_value = (cur_byte >> ((-bit_index - bit_depth) % 8)) & (2 ** bit_index - 1)
    color_tuple = [color_value * int(256 / 2 ** bit_depth)] * 3
    bit_index += bit_depth
    cur_byte = img_data[bit_index // 8]
    color_value = (cur_byte >> ((-bit_index - bit_depth) % 8)) & (2 ** bit_index - 1)
    color_tuple.append(color_value)
    return color_tuple

def _color_type_6_to_RGBA(img_data, bit_index, bit_depth):
    color_tuple = []
    for n in range(_get_num_color_channels(6)):
        cur_byte = img_data[bit_index // 8]
        color_tuple.append(cur_byte)
        bit_index += bit_depth
    return color_tuple
        
'''
Converts the unfiltered image data into an array of pixels with RGBA color values.
'''
def convert_to_pixel_grid(img_data, img_width, img_height, bit_depth, color_type, palette=None):
    assert bit_depth != 16, '16-bit colors are not supported yet.'
    
    if color_type == 3:
        assert palette

    pixel_grid = []
    num_channels = _get_num_color_channels(color_type)
    bit_index = 0
    for y in range(img_height):
        pixel_grid.append([])
        for x in range(img_width):

            if color_type == 3:
                color_tuple = _color_type_3_to_RGBA(img_data, bit_index, bit_depth, palette)

            elif color_type == 6:
                color_tuple = _color_type_6_to_RGBA(img_data, bit_index, bit_depth)

            elif color_type == 0:
                color_tuple = _color_type_0_to_RGBA(img_data, bit_index, bit_depth)

            elif color_type == 2:
                color_tuple = _color_type_2_to_RGBA(img_data, bit_index, bit_depth)

            elif color_type == 4:
                color_tuple = _color_type_4_to_RGBA(img_data, bit_index, bit_depth)

            else:
                raise Exception('Unsupported color type: {color_type}.')

            pixel_grid[-1].append(color_tuple)
            bit_index += bit_depth * num_channels
    return pixel_grid

'''
Prints a colored pixel with the given RGB value to stdout.
'''
def print_colored_pixel(r, g, b):
    print(f'\x1b[38;2;{r};{g};{b}m{PIXEL_TEXT}', end='')

'''
Prints a colored pixel with the given RGBA value to stdout. The alpha value is approximately
displayed by adjusting the R, G, and B values accordingly.
'''
def print_colored_pixel_alpha(r, g, b, a):
    displayed_r = int(r * a / 255 + (255 - a if BACKGROUND_IS_WHITE else 0))
    displayed_g = int(g * a / 255 + (255 - a if BACKGROUND_IS_WHITE else 0))
    displayed_b = int(b * a / 255 + (255 - a if BACKGROUND_IS_WHITE else 0))
    print_colored_pixel(displayed_r, displayed_g, displayed_b)

'''
Main function.
'''
def main():
    chunks = None

    file_name = input(f'Enter file name: ')

    debug_print(f'Opening file now...')
    with open(file_name, 'rb') as f:

        read_validate_png_header(f)

        debug_print(f'Successfully validated PNG header.')
        debug_print(f'Reading chunks...')

        chunks = read_chunks(f)

        debug_print(f'Finished reading chunks. Closing file...')
    
    debug_print(f'Processing IHDR chunk...')

    img_width, img_height, bit_depth, color_type, _, _, interlace_method = process_IHDR_chunk(chunks[0])

    debug_print(f'Successfully processed IHDR chunk.')
    debug_print(f'''Read the following image info:
- Dimensions: {img_width} x {img_height}
- Sample depth: {bit_depth}
- Color type: {color_type}
- Interlace method: {interlace_method}''')
    
    palette = None
    if color_type == 3:
        debug_print(f'Processing PLTE chunk...')
        palette = process_PLTE_chunk([chunk for chunk in chunks if chunk[0] == CHUNK_TYPE_PLTE][0])
        debug_print(f'Successfully loaded color palette.')
    
    debug_print(f'Uncompressing image data...')
    
    raw_img_data = uncompress_image_bytes(coalesce_image_data(chunks))

    debug_print(f'Length of uncompressed data: {len(raw_img_data)}')
    debug_print(f'Unfiltering image...')

    unfiltered_img_data = unfilter_image_data(raw_img_data, img_width, img_height, bit_depth, color_type, palette)

    debug_print(f'Converting to RGB...')

    pixel_data = convert_to_pixel_grid(unfiltered_img_data, img_width, img_height, bit_depth, color_type, palette)

    for x in range(img_width + 2):
        print_colored_pixel(255, 255, 255)
    print('')
    for y in range(img_height):
        print_colored_pixel(255, 255, 255)
        for x in range(img_width):
            r, g, b, a = pixel_data[y][x]
            print_colored_pixel_alpha(r, g, b, a)
        print_colored_pixel(255, 255, 255)
        print('')
    for x in range(img_width + 2):
        print_colored_pixel(255, 255, 255)
    print('')
        
if __name__ == '__main__':
    main()