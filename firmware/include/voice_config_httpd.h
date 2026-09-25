#ifndef VOICE_CONFIG_HTTPD_H
#define VOICE_CONFIG_HTTPD_H

#include "voice_settings.h"

bool voice_config_parse_form(char *body, voice_settings_t *next);

void voice_config_httpd_start(voice_settings_t *settings);
void voice_config_httpd_setup_ap_started(void);

#endif
