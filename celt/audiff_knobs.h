#ifndef AUDIFF_KNOBS_H
#define AUDIFF_KNOBS_H

/* v05 / SenaV m2 knob surface.  Defaults = frozen v05 profile
   (adapt4=1000, tb40, sustain gate@80, td0); env vars still override.

   v05 completion: tonality-boost fade over bitrate (piecewise linear,
   <=128k full boost, 192k 30%, >=320k off; judged on the nominal
   bitrate so it is constant within an encode). */

int audiff_knob_adapt_intensity(void);
int audiff_knob_vbr_tboost(void);
int audiff_knob_sustain_gate(void);
int audiff_knob_sustain_ratio(void);
int audiff_knob_vbr_tdecay(void);
int audiff_knob_tonal_fade(void);
int audiff_knob_tonal_fade_lo(void);
int audiff_knob_tonal_fade_mid(void);
int audiff_knob_tonal_fade_midval(void);
int audiff_knob_tonal_fade_hi(void);
int audiff_knob_adapt_budgetgate(void);
int audiff_knob_budgetgate_base(void);

#endif
