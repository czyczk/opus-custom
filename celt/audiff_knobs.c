#include "audiff_knobs.h"
#include <stdlib.h>

static int env_int(const char *name, int dflt)
{
   const char *p = getenv(name);
   if (!p || !p[0]) return dflt;
   return atoi(p);
}

int audiff_knob_adapt_intensity(void)
{
   static int v = -2;
   if (v == -2) v = env_int("AUDIFF_ADAPT_INTENSITY", 1000);
   return v;
}

int audiff_knob_vbr_tboost(void)
{
   static int v = -2;
   if (v == -2) v = env_int("AUDIFF_VBR_TBOOST", 40);
   return v;
}

int audiff_knob_sustain_gate(void)
{
   static int v = -2;
   if (v == -2) v = env_int("AUDIFF_TBOOST_SUSTAIN_GATE", 1);
   return v;
}

int audiff_knob_sustain_ratio(void)
{
   static int v = -2;
   if (v == -2) v = env_int("AUDIFF_TBOOST_SUSTAIN_RATIO", 80);
   return v;
}

int audiff_knob_vbr_tdecay(void)
{
   static int v = -2;
   if (v == -2) v = env_int("AUDIFF_VBR_TDECAY", 0);
   return v;
}

int audiff_knob_tonal_fade(void)
{
   static int v = -2;
   if (v == -2) v = env_int("AUDIFF_TONAL_FADE", 1);
   return v;
}

int audiff_knob_tonal_fade_lo(void)
{
   static int v = -2;
   if (v == -2) v = env_int("AUDIFF_TONAL_FADE_LO", 128000);
   return v;
}

int audiff_knob_tonal_fade_mid(void)
{
   static int v = -2;
   if (v == -2) v = env_int("AUDIFF_TONAL_FADE_MID", 192000);
   return v;
}

int audiff_knob_tonal_fade_midval(void)
{
   static int v = -2;
   if (v == -2) v = env_int("AUDIFF_TONAL_FADE_MIDVAL", 30);
   return v;
}

int audiff_knob_tonal_fade_hi(void)
{
   static int v = -2;
   if (v == -2) v = env_int("AUDIFF_TONAL_FADE_HI", 320000);
   return v;
}
