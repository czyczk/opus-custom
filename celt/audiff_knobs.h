#ifndef AUDIFF_KNOBS_H
#define AUDIFF_KNOBS_H

/* v06 / SenaV m3 knob surface.  Defaults = frozen profile
   (adapt4=1000 restructured + budget gate on, tb40, sustain gate@80,
   td0, tonal fade on, theta floor 2730@b>=16); env vars still override.
   Topband stereo (0=off, else floor rate in kbps) is armed by the
   caller when the budget tier calls for it. */

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
int audiff_knob_theta_floor(void);
int audiff_knob_theta_floor_band(void);
int audiff_knob_topband_stereo(void);

#endif
