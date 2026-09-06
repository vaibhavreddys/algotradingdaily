#!/bin/bash
# tmux status-bar segment: OpenAlgo health (service + HTTP + broker token).
# Refreshed by tmux every status-interval; must stay fast (2s curl cap).

svc=$(systemctl is-active openalgo 2>/dev/null)
http=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 http://127.0.0.1:5000/ 2>/dev/null)

if [ "$svc" = "active" ] && [ "$http" = "200" ]; then
    printf "#[fg=colour46,bold]OA:UP#[fg=colour240]|#[default]"
elif [ "$svc" = "active" ]; then
    printf "#[fg=colour214,bold]OA:%s#[fg=colour240]|#[default]" "${http:-ERR}"
else
    printf "#[fg=colour196,bold]OA:DOWN#[fg=colour240]|#[default]"
fi
