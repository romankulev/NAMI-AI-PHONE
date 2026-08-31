#!/usr/bin/env bash
set -euo pipefail

app_dir="${HOME}/apps/openai-realtime-miniapp"
env_file="${app_dir}/.env"
env_template="${app_dir}/deploy/server.env.example"

printf 'Вставьте OPENAI_API_KEY (ввод скрыт) и нажмите Enter: '
IFS= read -r -s realtime_api_key
printf '\n'

if [[ -z "${realtime_api_key}" ]]; then
  echo 'Ключ не введён. Конфигурация не изменена.'
  exit 1
fi

umask 077
source_file="${env_file}"
if [[ ! -f "${source_file}" ]]; then
  source_file="${env_template}"
fi

temp_file="$(mktemp "${app_dir}/.env.XXXXXX")"
{
  printf 'OPENAI_API_KEY=%s\n' "${realtime_api_key}"
  grep -v '^OPENAI_API_KEY=' "${source_file}"
} > "${temp_file}"
mv "${temp_file}" "${env_file}"
chmod 600 "${env_file}"

unset realtime_api_key
systemctl --user restart openai-realtime-miniapp.service
echo 'Ключ сохранён с правами 600, сервис перезапущен.'
