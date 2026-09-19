#ifndef AUDIFF_KNOBS_H
#define AUDIFF_KNOBS_H

/* v05 / SenaV m2 knob surface.  Defaults = frozen v05 profile
   (adapt4=1000, tb40, sustain gate@80, td0); env vars still override. */

int audiff_knob_adapt_intensity(void);
int audiff_knob_vbr_tboost(void);
int audiff_knob_sustain_gate(void);
int audiff_knob_sustain_ratio(void);
int audiff_knob_vbr_tdecay(void);

#endif
