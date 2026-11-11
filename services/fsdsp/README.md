# fsdsp — FreeSounder live DSP ALSA plugin

`libasound_module_pcm_fsdsp.so` is an ALSA external filter (`type fsdsp`). It applies
bass and treble shelves (120 Hz / 4 kHz), loudness and balance to float stereo. It
re-reads `/run/freesounder/dsp-params` while audio plays, so tone changes from the app
take effect within about 50 ms. The device is not re-opened and snapclient does not
restart. A change crossfades over 1024 frames, so there are no clicks. When every
setting is flat the samples pass through unchanged (bit-exact).

Params file: `fsdsp1 <bass dB> <treble dB> <balance -10..10> <loudness 0|1>` (written by freesounderd).

Build: `make` (needs gcc and libasound2-dev). The image builds it for the board in
`images/player/pi-gen/stage-freesounder/02-python/02-run-chroot.sh`.
Tests: `services/freesounderd/tests/test_live_dsp.py` (real ALSA in CI).
