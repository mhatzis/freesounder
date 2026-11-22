/*
 * FreeSounder live DSP: an ALSA external filter plugin (pcm type "fsdsp").
 *
 * Bass and treble shelves, loudness and balance on float stereo. The settings
 * come from a small text file (default /run/freesounder/dsp-params) that is
 * re-read whenever it changes, so tone changes apply while audio plays: no
 * ALSA re-open, no snapclient restart. Each change crossfades over ~46 ms
 * (2048 frames); a change that arrives mid-fade waits for that fade to end
 * (latest value wins), so rapid slider moves never jump.
 *
 * Bass: low shelf at 150 Hz. Treble: high shelf at 4 kHz. Loudness ("Bass
 * boost"): +6 dB low shelf at 100 Hz plus +1.5 dB air at 12 kHz; bass and
 * loudness together are capped at +12 dB. There is no fixed pre-gain: a
 * fixed cut of half the boost made everything the speaker can actually play
 * (100 Hz and up) quieter, so bass and loudness sounded like less bass.
 * snapclient's software volume already leaves headroom below 100 %, and a
 * stereo-linked peak limiter (ceiling -0.5 dBFS, instant attack, ~80 ms
 * release) catches the rest, so the output never clips (safe for amplifier
 * HATs). With every setting flat the samples pass through unchanged.
 *
 * Params file (one line, whitespace separated, missing fields = 0):
 *   fsdsp1 <bass dB> <treble dB> <balance -10..10> <loudness 0|1>
 *
 * asound.conf:
 *   pcm.x { type fsdsp slave.pcm "y" params "/run/freesounder/dsp-params" }
 */
#include <alsa/asoundlib.h>
#include <alsa/pcm_external.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

#define NBQ 4         /* low shelf, high shelf, loudness low, loudness air */
#define RAMP 2048     /* frames to crossfade filters and gains */
#define CHECK_EVERY 2048 /* frames between params file checks */
#ifndef KEEPALIVE_AFTER
#define KEEPALIVE_AFTER 20.0 /* s of silence before the keep-alive starts (tests: -D) */
#endif
#define KEEPALIVE_HZ 30.0
#define KEEPALIVE_FADE 0.3   /* s, fade-in time constant */
#define SILENT 1e-5f         /* about -100 dBFS */

typedef struct { double b0, b1, b2, a1, a2; } coef_t;
typedef struct { double x1, x2, y1, y2; } state_t;

typedef struct {
	float bass, treble, balance;
	int loudness;
	float keepalive_db; /* < 0: keep a Bluetooth speaker awake at this level; 0: off */
} params_t;

typedef struct {
	snd_pcm_extplug_t ext;
	char *path;
	unsigned int rate;
	params_t p;
	coef_t cur[NBQ], old[NBQ];
	state_t st[2][NBQ], st_old[2][NBQ];
	double gl, gr, pre;        /* target gains */
	double gl0, gr0, pre0;     /* gains at ramp start */
	unsigned int ramp;         /* frames left in ramp */
	params_t pending;          /* arrived mid-ramp; applied when it ends */
	int has_pending;
	double lim;                /* limiter gain, 1.0 = idle */
	double lim_release;        /* per-frame recovery factor */
	unsigned long since_check;
	struct timespec mtime;
	off_t size;
	int have_file;
	/* Keep-alive (0.6.19): after KEEPALIVE_AFTER s of digital silence, a
	 * 30 Hz tone at keepalive_db keeps a Bluetooth speaker's auto standby
	 * from firing. Off the moment real audio comes back. */
	unsigned long quiet;       /* consecutive silent frames */
	double ka_gain;            /* current keep-alive amplitude */
	double ka_phase;
} fsdsp_t;

static int params_equal(const params_t *a, const params_t *b)
{
	return a->bass == b->bass && a->treble == b->treble && a->balance == b->balance &&
	       a->loudness == b->loudness;
}

static void identity(coef_t *c)
{
	c->b0 = 1.0; c->b1 = c->b2 = c->a1 = c->a2 = 0.0;
}

/* RBJ cookbook shelf, S = 1. */
static void shelf(coef_t *c, int high, double f0, double gain_db, double fs)
{
	if (fabs(gain_db) < 1e-3 || f0 >= fs * 0.45) {
		identity(c);
		return;
	}
	double A = pow(10.0, gain_db / 40.0);
	double w0 = 2.0 * M_PI * f0 / fs;
	double cw = cos(w0), sw = sin(w0);
	double alpha = sw / 2.0 * sqrt(2.0);
	double sa = 2.0 * sqrt(A) * alpha;
	double b0, b1, b2, a0, a1, a2;
	if (!high) {
		b0 = A * ((A + 1) - (A - 1) * cw + sa);
		b1 = 2 * A * ((A - 1) - (A + 1) * cw);
		b2 = A * ((A + 1) - (A - 1) * cw - sa);
		a0 = (A + 1) + (A - 1) * cw + sa;
		a1 = -2 * ((A - 1) + (A + 1) * cw);
		a2 = (A + 1) + (A - 1) * cw - sa;
	} else {
		b0 = A * ((A + 1) + (A - 1) * cw + sa);
		b1 = -2 * A * ((A - 1) + (A + 1) * cw);
		b2 = A * ((A + 1) + (A - 1) * cw - sa);
		a0 = (A + 1) - (A - 1) * cw + sa;
		a1 = 2 * ((A - 1) - (A + 1) * cw);
		a2 = (A + 1) - (A - 1) * cw - sa;
	}
	c->b0 = b0 / a0; c->b1 = b1 / a0; c->b2 = b2 / a0;
	c->a1 = a1 / a0; c->a2 = a2 / a0;
}

#define BASS_HZ 150.0
#define TREBLE_HZ 4000.0
#define LOUD_LOW_HZ 100.0
#define LOUD_LOW_DB 6.0
#define LOUD_AIR_HZ 12000.0
#define LOUD_AIR_DB 1.5
#define MAX_LOW_DB 12.0 /* bass + loudness together */

static void design(fsdsp_t *d)
{
	double fs = d->rate ? d->rate : 44100;
	double bass = d->p.bass, treble = d->p.treble;
	double loud_low = 0.0;
	if (d->p.loudness) {
		loud_low = LOUD_LOW_DB;
		if (bass > 0 && bass + loud_low > MAX_LOW_DB)
			loud_low = MAX_LOW_DB - bass;
		if (loud_low < 0)
			loud_low = 0;
	}
	shelf(&d->cur[0], 0, BASS_HZ, bass, fs);
	shelf(&d->cur[1], 1, TREBLE_HZ, treble, fs);
	shelf(&d->cur[2], 0, LOUD_LOW_HZ, loud_low, fs);
	shelf(&d->cur[3], 1, LOUD_AIR_HZ, d->p.loudness ? LOUD_AIR_DB : 0.0, fs);
	/* No fixed pre-gain (see the top of this file): the limiter in
	 * transfer() keeps peaks below full scale. */
	d->pre = 1.0;
	double b = d->p.balance;
	if (b < -10) b = -10;
	if (b > 10) b = 10;
	d->gl = b <= 0 ? 1.0 : (10.0 - b) / 10.0;
	d->gr = b >= 0 ? 1.0 : (10.0 + b) / 10.0;
}

static int is_flat(const fsdsp_t *d)
{
	return d->p.bass == 0 && d->p.treble == 0 && d->p.balance == 0 && !d->p.loudness;
}

static int read_params(const char *path, params_t *out)
{
	FILE *f = fopen(path, "r");
	if (!f)
		return -1;
	char tag[16] = "";
	params_t p = {0, 0, 0, 0, 0};
	int n = fscanf(f, "%15s %f %f %f %d %f", tag, &p.bass, &p.treble, &p.balance, &p.loudness,
		       &p.keepalive_db);
	fclose(f);
	if (n < 1 || strcmp(tag, "fsdsp1") != 0)
		return -1;
	if (n < 6 || !isfinite(p.keepalive_db) || p.keepalive_db >= 0)
		p.keepalive_db = 0; /* older params files: off */
	if (p.keepalive_db < -90) p.keepalive_db = -90;
	if (p.keepalive_db < 0 && p.keepalive_db > -30) p.keepalive_db = -30;
	if (!isfinite(p.bass)) p.bass = 0;
	if (!isfinite(p.treble)) p.treble = 0;
	if (!isfinite(p.balance)) p.balance = 0;
	if (p.bass > 12) p.bass = 12;
	if (p.bass < -12) p.bass = -12;
	if (p.treble > 12) p.treble = 12;
	if (p.treble < -12) p.treble = -12;
	*out = p;
	return 0;
}

/* Start a crossfade from the current filters/gains to new params. */
static void apply_params(fsdsp_t *d, const params_t *p)
{
	memcpy(d->old, d->cur, sizeof d->cur);
	memcpy(d->st_old, d->st, sizeof d->st);
	d->gl0 = d->gl; d->gr0 = d->gr; d->pre0 = d->pre;
	d->p = *p;
	design(d);
	d->ramp = RAMP;
}

static void check_params(fsdsp_t *d, int force)
{
	struct stat sb;
	if (stat(d->path, &sb) != 0) {
		if (d->have_file) {
			params_t flat = {0, 0, 0, 0, 0};
			d->have_file = 0;
			apply_params(d, &flat);
		}
		return;
	}
	if (!force && d->have_file && sb.st_mtim.tv_sec == d->mtime.tv_sec &&
	    sb.st_mtim.tv_nsec == d->mtime.tv_nsec && sb.st_size == d->size)
		return;
	params_t p;
	if (read_params(d->path, &p) != 0)
		return;
	d->have_file = 1;
	d->mtime = sb.st_mtim;
	d->size = sb.st_size;
	/* The keep-alive level needs no filter crossfade. */
	d->p.keepalive_db = p.keepalive_db;
	if (!force && d->ramp) {
		d->pending = p;
		d->has_pending = 1;
		return;
	}
	if (force || !params_equal(&p, &d->p))
		apply_params(d, &p);
}

/* Called between transfers: start a waiting change once the fade is done. */
static void apply_pending(fsdsp_t *d)
{
	if (d->has_pending && d->ramp == 0) {
		d->has_pending = 0;
		if (!params_equal(&d->pending, &d->p))
			apply_params(d, &d->pending);
	}
}

static inline double run_bq(const coef_t *c, state_t *s, double x)
{
	double y = c->b0 * x + c->b1 * s->x1 + c->b2 * s->x2 - c->a1 * s->y1 - c->a2 * s->y2;
	s->x2 = s->x1; s->x1 = x;
	s->y2 = s->y1; s->y1 = y;
	return y;
}

#define CEILING 0.944 /* -0.5 dBFS */

static inline double process(fsdsp_t *d, int ch, double x, unsigned int ramp)
{
	double y = x * d->pre;
	int k;
	for (k = 0; k < NBQ; k++)
		y = run_bq(&d->cur[k], &d->st[ch][k], y);
	y *= ch ? d->gr : d->gl;
	if (ramp) {
		double yo = x * d->pre0;
		for (k = 0; k < NBQ; k++)
			yo = run_bq(&d->old[k], &d->st_old[ch][k], yo);
		yo *= ch ? d->gr0 : d->gl0;
		double t = (double)(RAMP - ramp) / RAMP;
		y = yo + (y - yo) * t;
	}
	return y;
}

/* Keep-alive sample to add after this frame (0 while there is audio). */
static inline float keepalive(fsdsp_t *d, float xl, float xr)
{
	if (d->p.keepalive_db >= 0) {
		d->quiet = 0;
		d->ka_gain = 0;
		return 0.0f;
	}
	double rate = d->rate ? d->rate : 44100;
	if (fabsf(xl) > SILENT || fabsf(xr) > SILENT) {
		d->quiet = 0;   /* music: off at once */
		d->ka_gain = 0;
		return 0.0f;
	}
	if (d->quiet < (unsigned long)(KEEPALIVE_AFTER * rate))
		d->quiet++;
	double target = d->quiet >= (unsigned long)(KEEPALIVE_AFTER * rate)
				? pow(10.0, d->p.keepalive_db / 20.0)
				: 0.0;
	d->ka_gain += (target - d->ka_gain) * (1.0 - exp(-1.0 / (KEEPALIVE_FADE * rate)));
	if (d->ka_gain < 1e-9)
		return 0.0f;
	double v = d->ka_gain * sin(d->ka_phase);
	d->ka_phase += 2.0 * M_PI * KEEPALIVE_HZ / rate;
	if (d->ka_phase > 2.0 * M_PI)
		d->ka_phase -= 2.0 * M_PI;
	return (float)v;
}

static snd_pcm_sframes_t fsdsp_transfer(snd_pcm_extplug_t *ext,
					const snd_pcm_channel_area_t *dst, snd_pcm_uframes_t doff,
					const snd_pcm_channel_area_t *src, snd_pcm_uframes_t soff,
					snd_pcm_uframes_t size)
{
	fsdsp_t *d = ext->private_data;
	d->since_check += size;
	if (d->since_check >= CHECK_EVERY) {
		d->since_check = 0;
		check_params(d, 0);
	}
	apply_pending(d);
	const char *sp[2];
	char *dp[2];
	unsigned int sstep[2], dstep[2], ch;
	for (ch = 0; ch < 2; ch++) {
		sp[ch] = (const char *)src[ch].addr + (src[ch].first + soff * src[ch].step) / 8;
		dp[ch] = (char *)dst[ch].addr + (dst[ch].first + doff * dst[ch].step) / 8;
		sstep[ch] = src[ch].step / 8;
		dstep[ch] = dst[ch].step / 8;
	}
	int flat = is_flat(d) && d->ramp == 0 && d->lim >= 1.0;
	snd_pcm_uframes_t i;
	for (i = 0; i < size; i++) {
		float xl = *(const float *)sp[0], xr = *(const float *)sp[1];
		float ka = keepalive(d, xl, xr);
		if (flat) {
			*(float *)dp[0] = xl + ka;
			*(float *)dp[1] = xr + ka;
		} else {
			double yl = process(d, 0, xl, d->ramp);
			double yr = process(d, 1, xr, d->ramp);
			if (d->ramp)
				d->ramp--;
			double peak = fabs(yl) > fabs(yr) ? fabs(yl) : fabs(yr);
			double want = peak * d->lim > CEILING ? CEILING / peak : 1.0;
			if (want < d->lim)
				d->lim = want; /* instant attack */
			else
				d->lim = d->lim + (1.0 - d->lim) * d->lim_release;
			if (d->lim > 1.0)
				d->lim = 1.0;
			if (peak * d->lim > CEILING) /* never above the ceiling */
				d->lim = CEILING / peak;
			*(float *)dp[0] = (float)(yl * d->lim) + ka;
			*(float *)dp[1] = (float)(yr * d->lim) + ka;
		}
		sp[0] += sstep[0];
		sp[1] += sstep[1];
		dp[0] += dstep[0];
		dp[1] += dstep[1];
	}
	return size;
}

static int fsdsp_init(snd_pcm_extplug_t *ext)
{
	fsdsp_t *d = ext->private_data;
	d->rate = ext->rate;
	d->lim = 1.0;
	d->quiet = 0;
	d->ka_gain = 0;
	d->ka_phase = 0;
	/* ~80 ms time constant back to unity. */
	d->lim_release = 1.0 - exp(-1.0 / (0.080 * (d->rate ? d->rate : 44100)));
	memset(d->st, 0, sizeof d->st);
	memset(d->st_old, 0, sizeof d->st_old);
	design(d);
	d->ramp = 0;
	d->gl0 = d->gl; d->gr0 = d->gr; d->pre0 = d->pre;
	check_params(d, 1);
	d->ramp = 0; /* no fade-in on open */
	return 0;
}

static int fsdsp_close(snd_pcm_extplug_t *ext)
{
	fsdsp_t *d = ext->private_data;
	free(d->path);
	free(d);
	return 0;
}

static const snd_pcm_extplug_callback_t fsdsp_callback = {
	.transfer = fsdsp_transfer,
	.init = fsdsp_init,
	.close = fsdsp_close,
};

SND_PCM_PLUGIN_DEFINE_FUNC(fsdsp)
{
	snd_config_iterator_t i, next;
	snd_config_t *slave = NULL;
	const char *path = "/run/freesounder/dsp-params";
	int err;

	snd_config_for_each(i, next, conf) {
		snd_config_t *n = snd_config_iterator_entry(i);
		const char *id;
		if (snd_config_get_id(n, &id) < 0)
			continue;
		if (strcmp(id, "comment") == 0 || strcmp(id, "type") == 0 || strcmp(id, "hint") == 0)
			continue;
		if (strcmp(id, "slave") == 0) {
			slave = n;
			continue;
		}
		if (strcmp(id, "params") == 0) {
			if (snd_config_get_string(n, &path) < 0) {
				SNDERR("fsdsp: params must be a string");
				return -EINVAL;
			}
			continue;
		}
		SNDERR("fsdsp: unknown field %s", id);
		return -EINVAL;
	}
	if (!slave) {
		SNDERR("fsdsp: no slave defined");
		return -EINVAL;
	}
	fsdsp_t *d = calloc(1, sizeof(*d));
	if (!d)
		return -ENOMEM;
	d->path = strdup(path);
	d->ext.version = SND_PCM_EXTPLUG_VERSION;
	d->ext.name = "FreeSounder live DSP";
	d->ext.callback = &fsdsp_callback;
	d->ext.private_data = d;
	err = snd_pcm_extplug_create(&d->ext, name, root, slave, stream, mode);
	if (err < 0) {
		free(d->path);
		free(d);
		return err;
	}
	snd_pcm_extplug_set_param(&d->ext, SND_PCM_EXTPLUG_HW_CHANNELS, 2);
	snd_pcm_extplug_set_slave_param(&d->ext, SND_PCM_EXTPLUG_HW_CHANNELS, 2);
	snd_pcm_extplug_set_param(&d->ext, SND_PCM_EXTPLUG_HW_FORMAT, SND_PCM_FORMAT_FLOAT);
	snd_pcm_extplug_set_slave_param(&d->ext, SND_PCM_EXTPLUG_HW_FORMAT, SND_PCM_FORMAT_FLOAT);
	*pcmp = d->ext.pcm;
	return 0;
}

SND_PCM_PLUGIN_SYMBOL(fsdsp);
