/*
 * FreeSounder live DSP: an ALSA external filter plugin (pcm type "fsdsp").
 *
 * Bass and treble shelves, loudness and balance on float stereo. The settings
 * come from a small text file (default /run/freesounder/dsp-params) that is
 * re-read whenever it changes, so tone changes apply while audio plays: no
 * ALSA re-open, no snapclient restart. Gains ramp over ~20 ms to avoid
 * clicks. With every setting flat the samples pass through unchanged.
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
#define RAMP 1024     /* frames to ramp gains / crossfade filters */
#define CHECK_EVERY 2048 /* frames between params file checks */

typedef struct { double b0, b1, b2, a1, a2; } coef_t;
typedef struct { double x1, x2, y1, y2; } state_t;

typedef struct {
	float bass, treble, balance;
	int loudness;
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
	unsigned long since_check;
	struct timespec mtime;
	off_t size;
	int have_file;
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

static void design(fsdsp_t *d)
{
	double fs = d->rate ? d->rate : 44100;
	double bass = d->p.bass, treble = d->p.treble;
	shelf(&d->cur[0], 0, 120.0, bass, fs);
	shelf(&d->cur[1], 1, 4000.0, treble, fs);
	shelf(&d->cur[2], 0, 60.0, d->p.loudness ? 6.0 : 0.0, fs);
	shelf(&d->cur[3], 1, 12000.0, d->p.loudness ? 1.5 : 0.0, fs);
	/* Headroom: take back half of the largest boost so bass boost does not clip. */
	double boost = 0.0;
	double lows = (bass > 0 ? bass : 0) + (d->p.loudness ? 6.0 : 0.0);
	double highs = (treble > 0 ? treble : 0) + (d->p.loudness ? 1.5 : 0.0);
	boost = lows > highs ? lows : highs;
	d->pre = pow(10.0, -0.5 * boost / 20.0);
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
	params_t p = {0, 0, 0, 0};
	int n = fscanf(f, "%15s %f %f %f %d", tag, &p.bass, &p.treble, &p.balance, &p.loudness);
	fclose(f);
	if (n < 1 || strcmp(tag, "fsdsp1") != 0)
		return -1;
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
			params_t flat = {0, 0, 0, 0};
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
	if (force || !params_equal(&p, &d->p))
		apply_params(d, &p);
}

static inline double run_bq(const coef_t *c, state_t *s, double x)
{
	double y = c->b0 * x + c->b1 * s->x1 + c->b2 * s->x2 - c->a1 * s->y1 - c->a2 * s->y2;
	s->x2 = s->x1; s->x1 = x;
	s->y2 = s->y1; s->y1 = y;
	return y;
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
	unsigned int ch;
	int flat = is_flat(d) && d->ramp == 0;
	for (ch = 0; ch < 2; ch++) {
		const char *sp = (const char *)src[ch].addr + (src[ch].first + soff * src[ch].step) / 8;
		char *dp = (char *)dst[ch].addr + (dst[ch].first + doff * dst[ch].step) / 8;
		unsigned int sstep = src[ch].step / 8, dstep = dst[ch].step / 8;
		double g = ch ? d->gr : d->gl, g0 = ch ? d->gr0 : d->gl0;
		unsigned int ramp = d->ramp;
		snd_pcm_uframes_t i;
		for (i = 0; i < size; i++) {
			float x = *(const float *)sp;
			float out;
			if (flat) {
				out = x;
			} else {
				double y = x * d->pre;
				int k;
				for (k = 0; k < NBQ; k++)
					y = run_bq(&d->cur[k], &d->st[ch][k], y);
				y *= g;
				if (ramp) {
					double yo = x * d->pre0;
					for (k = 0; k < NBQ; k++)
						yo = run_bq(&d->old[k], &d->st_old[ch][k], yo);
					yo *= g0;
					double t = (double)(RAMP - ramp) / RAMP;
					y = yo + (y - yo) * t;
					ramp--;
				}
				out = (float)y;
			}
			*(float *)dp = out;
			sp += sstep;
			dp += dstep;
		}
		if (ch == 1)
			d->ramp = ramp;
	}
	return size;
}

static int fsdsp_init(snd_pcm_extplug_t *ext)
{
	fsdsp_t *d = ext->private_data;
	d->rate = ext->rate;
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
