# libasound_module_pcm_fsdsp.so: FreeSounder live tone/balance ALSA plugin.
CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra
CFLAGS += -fPIC -DPIC
ALSA_CFLAGS ?= $(shell pkg-config --cflags alsa 2>/dev/null)
ALSA_LIBS ?= $(shell pkg-config --libs alsa 2>/dev/null || echo -lasound)
LIBDIR ?= $(shell pkg-config --variable=libdir alsa 2>/dev/null || echo /usr/lib)/alsa-lib

libasound_module_pcm_fsdsp.so: fsdsp.c
	$(CC) $(CFLAGS) $(ALSA_CFLAGS) -shared -o $@ $< $(ALSA_LIBS) -lm

install: libasound_module_pcm_fsdsp.so
	install -D -m 0644 $< $(DESTDIR)$(LIBDIR)/libasound_module_pcm_fsdsp.so

clean:
	rm -f libasound_module_pcm_fsdsp.so

.PHONY: install clean
