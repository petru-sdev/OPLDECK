/* Independent, table-free reference for the USBExtreme checksum.
 * Reference: OPL src/system.c USBA_crc32. Unsigned arithmetic makes wrapping defined.
 * This test utility processes ASCII titles of at most 32 bytes, including NUL.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv)
{
    for (int arg = 1; arg < argc; ++arg) {
        size_t length = strlen(argv[arg]);
        if (length > 32) return 2;
        uint32_t crc = 0;
        for (size_t pos = 0; pos <= length; ++pos) {
            unsigned char byte = (unsigned char)argv[arg][pos];
            if (byte >= 128) return 2;
            crc ^= (uint32_t)byte << 24;
            for (int bit = 0; bit < 8; ++bit) {
                uint32_t top = crc & UINT32_C(0x80000000);
                crc <<= 1;
                if (top) crc ^= UINT32_C(0x04C11DB7);
            }
        }
        printf("%08X\n", (unsigned int)crc);
    }
    return 0;
}
