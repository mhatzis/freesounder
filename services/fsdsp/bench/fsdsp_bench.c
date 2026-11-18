/*
 * fsdsp_bench: play N seconds through an ALSA PCM (normally "freesounder"
 * with a null or file sink) while another thread rewrites the fsdsp params
 * file every few ms, sweeping bass/treble/balance/loudness. Reports the
 * worst time one period took to go through the chain against the period's
 * real-time budget, plus any xrun/error from snd_pcm_writei.
 *
 *   fsdsp_bench <pcm> <params-file> <seconds> <sweep-ms> [rate]
 * Output (one line, key=value): periods, budget_ms, max_ms, p99_ms, mean_ms, xruns, errors
 */
#include <alsa/asoundlib.h>
#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static const char *params_path;
static volatile int running = 1;
static int sweep_ms = 20;

static double now_ms(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}

static void *sweeper(void *arg)
{
	(void)arg;
	char tmp[4096];
	snprintf(tmp, sizeof tmp, "%s.tmp", params_path);
	unsigned int i = 0;
	while (running) {
		FILE *f = fopen(tmp, "w");
		if (f) {
			double t = i * 0.37;
			fprintf(f, "fsdsp1 %.1f %.1f %.1f %d\n", 10 * sin(t), 10 * cos(t * 1.3),
				10 * sin(t * 0.7), (i / 7) % 2);
			fclose(f);
			rename(tmp, params_path);
		}
		i++;
		usleep(sweep_ms * 1000);
	}
	return NULL;
}

static int cmp(const void *a, const void *b)
{
	double x = *(const double *)a, y = *(const double *)b;
	return x < y ? -1 : x > y;
}

int main(int argc, char **argv)
{
	if (argc < 5) {
		fprintf(stderr, "usage: %s <pcm> <params> <seconds> <sweep-ms> [rate]\n", argv[0]);
		return 2;
	}
	params_path = argv[2];
	double seconds = atof(argv[3]);
	sweep_ms = atoi(argv[4]);
	unsigned int rate = argc > 5 ? (unsigned)atoi(argv[5]) : 44100;
	snd_pcm_t *pcm;
	int err = snd_pcm_open(&pcm, argv[1], SND_PCM_STREAM_PLAYBACK, 0);
	if (err < 0) {
		fprintf(stderr, "open %s: %s\n", argv[1], snd_strerror(err));
		return 1;
	}
	const snd_pcm_uframes_t period = 1024;
	err = snd_pcm_set_params(pcm, SND_PCM_FORMAT_S16_LE, SND_PCM_ACCESS_RW_INTERLEAVED, 2, rate, 1,
				 200000);
	if (err < 0) {
		fprintf(stderr, "set_params: %s\n", snd_strerror(err));
		return 1;
	}
	long periods = (long)(seconds * rate / period);
	short *buf = malloc(period * 4);
	double *times = malloc(sizeof(double) * periods);
	pthread_t th;
	pthread_create(&th, NULL, sweeper, NULL);
	long xruns = 0, errors = 0, frame = 0;
	for (long p = 0; p < periods; p++) {
		for (snd_pcm_uframes_t i = 0; i < period; i++, frame++) {
			short v = (short)(12000 * sin(2 * M_PI * 1000.0 * frame / rate) +
					  6000 * sin(2 * M_PI * 60.0 * frame / rate));
			buf[2 * i] = buf[2 * i + 1] = v;
		}
		double t0 = now_ms();
		snd_pcm_sframes_t n = snd_pcm_writei(pcm, buf, period);
		times[p] = now_ms() - t0;
		if (n == -EPIPE) {
			xruns++;
			snd_pcm_prepare(pcm);
		} else if (n < 0) {
			errors++;
			snd_pcm_recover(pcm, (int)n, 1);
		}
	}
	running = 0;
	pthread_join(th, NULL);
	snd_pcm_drain(pcm);
	snd_pcm_close(pcm);
	double sum = 0, max = 0;
	for (long p = 0; p < periods; p++) {
		sum += times[p];
		if (times[p] > max)
			max = times[p];
	}
	qsort(times, periods, sizeof(double), cmp);
	printf("periods=%ld budget_ms=%.3f max_ms=%.3f p99_ms=%.3f mean_ms=%.4f xruns=%ld errors=%ld\n",
	       periods, period * 1000.0 / rate, max, times[(long)(periods * 0.99)], sum / periods, xruns,
	       errors);
	return xruns || errors ? 3 : 0;
}
