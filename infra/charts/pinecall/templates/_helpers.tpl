{{/* The runtime's image, pinned to the commit it was built from. */}}
{{- define "pinecall.image" -}}
{{ .Values.image.repository }}:{{ required "image.tag: the commit the runtime image was built from" .Values.image.tag }}
{{- end -}}

{{/* What every runtime process reads, as v1's box.env and its sealed credentials gave it. */}}
{{- define "pinecall.env" -}}
- name: DATABASE_URL
  valueFrom: { secretKeyRef: { name: {{ .Values.postgres.secret }}, key: uri } }
- name: PINECALL_REDIS_URL
  valueFrom: { secretKeyRef: { name: pinecall, key: PINECALL_REDIS_URL } }
- name: PINECALL_VAULT_KEY
  valueFrom: { secretKeyRef: { name: pinecall, key: PINECALL_VAULT_KEY } }
- name: PINECALL_OPS_KEY
  valueFrom: { secretKeyRef: { name: pinecall, key: PINECALL_OPS_KEY } }
- name: LIVEKIT_API_KEY
  valueFrom: { secretKeyRef: { name: pinecall, key: LIVEKIT_API_KEY } }
- name: LIVEKIT_API_SECRET
  valueFrom: { secretKeyRef: { name: pinecall, key: LIVEKIT_API_SECRET } }
- name: LIVEKIT_URL
  value: ws://pinecall-livekit:7880
- name: LIVEKIT_PUBLIC_URL
  value: wss://{{ required "domains.production" .Values.domains.production }}
- name: PINECALL_DOMAIN
  value: {{ .Values.domains.production | quote }}
- name: PINECALL_SANDBOX_DOMAIN
  value: {{ required "domains.sandbox" .Values.domains.sandbox | quote }}
- name: PINECALL_DOMAINS
  value: {{ printf "%s, %s" .Values.domains.production .Values.domains.sandbox | quote }}
- name: PINECALL_GATEWAY_URL
  value: http://pinecall-gateway:8080
- name: PINECALL_RECORDINGS
  value: /var/lib/pinecall/recordings
{{- with .Values.store }}{{ if .bucket }}
- name: PINECALL_S3_ENDPOINT
  value: {{ required "store.endpoint" .endpoint | quote }}
- name: PINECALL_S3_REGION
  value: {{ required "store.region" .region | quote }}
- name: PINECALL_S3_ACCESS_KEY_ID
  value: {{ required "store.accessKeyId" .accessKeyId | quote }}
- name: PINECALL_RECORDINGS_BUCKET
  value: {{ .bucket | quote }}
- name: PINECALL_S3_SECRET_ACCESS_KEY
  valueFrom: { secretKeyRef: { name: pinecall, key: PINECALL_S3_SECRET_ACCESS_KEY } }
{{- end }}{{ end }}
{{- end -}}

{{/* A worker of a world: its fleet, its seats, its key, its health port for the pod's probes. */}}
{{- define "pinecall.workerEnv" -}}
- name: PINECALL_FLEET
  value: {{ index .root.Values.fleets .world | quote }}
- name: PINECALL_MAX_JOBS
  value: {{ .seats | quote }}
- name: PINECALL_WORKER_KEY
  valueFrom: { secretKeyRef: { name: pinecall-fleet-keys, key: {{ .world }} } }
- name: PINECALL_WORKER_HTTP_HOST
  value: 0.0.0.0
- name: PINECALL_WORKER_HTTP_PORT
  value: "8082"
{{- end -}}
