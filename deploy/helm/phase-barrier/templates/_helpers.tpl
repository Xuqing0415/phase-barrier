{{- define "phase-barrier.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "phase-barrier.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "phase-barrier.labels" -}}
app.kubernetes.io/name: {{ include "phase-barrier.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "phase-barrier.secretName" -}}
{{- .Values.sidecar.hmac.secretName | default (printf "%s-secrets" (include "phase-barrier.fullname" .)) -}}
{{- end -}}
