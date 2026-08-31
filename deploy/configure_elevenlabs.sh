#!/usr/bin/env bash
set -euo pipefail

app_dir="${HOME}/apps/openai-realtime-miniapp"
env_file="${app_dir}/.env"
env_template="${app_dir}/deploy/server.env.example"

printf 'Вставьте ELEVENLABS_API_KEY (ввод скрыт) и нажмите Enter: '
IFS= read -r -s elevenlabs_api_key
printf '\n'
printf 'Вставьте ELEVENLABS_VOICE_ID и нажмите Enter: '
IFS= read -r elevenlabs_voice_id

if [[ -z "${elevenlabs_api_key}" || -z "${elevenlabs_voice_id}" ]]; then
  echo 'Ключ или Voice ID не введён. Конфигурация не изменена.'
  exit 1
fi

umask 077
source_file="${env_file}"
if [[ ! -f "${source_file}" ]]; then
  source_file="${env_template}"
fi

temp_file="$(mktemp "${app_dir}/.env.XXXXXX")"
{
  printf 'ELEVENLABS_API_KEY=%s\n' "${elevenlabs_api_key}"
  printf 'ELEVENLABS_VOICE_ID=%s\n' "${elevenlabs_voice_id}"
  grep -v -E '^(ELEVENLABS_API_KEY|ELEVENLABS_VOICE_ID)=' "${source_file}"
} > "${temp_file}"
mv "${temp_file}" "${env_file}"
chmod 600 "${env_file}"

unset elevenlabs_api_key elevenlabs_voice_id

cd "${app_dir}"
set -a
source "${env_file}"
set +a
MINIAPP_ENV_PATH="${env_file}" "${app_dir}/.venv/bin/python" "${app_dir}/deploy/set_nami_prompt.py"
"${app_dir}/.venv/bin/python" "${app_dir}/deploy/sync_elevenlabs_agent.py" --env "${env_file}"
systemctl --user restart openai-realtime-miniapp.service
echo 'ElevenLabs Agent синхронизирован, сервис перезапущен.'
