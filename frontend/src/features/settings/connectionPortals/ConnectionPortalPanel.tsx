import { Check, Eye, EyeOff, X } from 'lucide-react'

import { Dropdown } from '../../../components/Dropdown'
import {
  COMPATIBLE_API_PRESET_OPTIONS,
  COMPATIBLE_API_PRESETS,
  CONNECTION_PORTAL_OPTIONS,
  CONNECTION_PORTALS,
  CONNECTION_SECRET_FIELDS,
  type CompatibleApiPresetId,
  type ConnectionPortalId,
  type ConnectionSecretId,
  type ConnectionSecretStatuses,
  type ConnectionSecretValues,
} from './connectionPortalConfig'

interface Props {
  selectedPortal: ConnectionPortalId
  values: ConnectionSecretValues
  statuses: ConnectionSecretStatuses
  showSecrets: boolean
  onPortalChange: (portal: ConnectionPortalId) => void
  onSecretChange: (secret: ConnectionSecretId, value: string) => void
  onToggleSecrets: () => void
  compatibleBaseUrl: string
  onCompatibleBaseUrlChange: (value: string) => void
  compatibleModelName: string
  onCompatibleModelNameChange: (value: string) => void
  onCompatibleModelNameCommit: (value: string) => void
  onCompatiblePresetChange: (preset: CompatibleApiPresetId) => void
}

export function ConnectionPortalPanel({
  selectedPortal,
  values,
  statuses,
  showSecrets,
  onPortalChange,
  onSecretChange,
  onToggleSecrets,
  compatibleBaseUrl,
  onCompatibleBaseUrlChange,
  compatibleModelName,
  onCompatibleModelNameChange,
  onCompatibleModelNameCommit,
  onCompatiblePresetChange,
}: Props) {
  const portal = CONNECTION_PORTALS.find((item) => item.id === selectedPortal) ?? CONNECTION_PORTALS[0]
  const compatiblePreset = (Object.entries(COMPATIBLE_API_PRESETS).find(
    ([id, preset]) => id !== 'custom' && preset.baseUrl === compatibleBaseUrl,
  )?.[0] ?? 'custom') as CompatibleApiPresetId

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <span className="st-group-label">Service</span>
        <Dropdown<ConnectionPortalId>
          value={portal.id}
          options={CONNECTION_PORTAL_OPTIONS}
          onChange={onPortalChange}
          ariaLabel="Portal"
        />
      </div>

      {portal.id === 'modelProviders' && (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <span className="st-group-label">Quick setup (optional)</span>
            <Dropdown<CompatibleApiPresetId>
              id="compatible-api-preset"
              value={compatiblePreset}
              options={COMPATIBLE_API_PRESET_OPTIONS}
              onChange={onCompatiblePresetChange}
              ariaLabel="Compatible provider preset"
            />
            <p className="text-xs leading-relaxed text-neutral-600">
              {COMPATIBLE_API_PRESETS[compatiblePreset].note}
            </p>
          </div>
          <div className="space-y-1.5">
            <label className="st-group-label" htmlFor="compatible-model-id">Model ID</label>
            <input
              id="compatible-model-id"
              type="text"
              autoComplete="off"
              maxLength={128}
              value={compatibleModelName}
              onChange={(event) => onCompatibleModelNameChange(event.target.value)}
              onBlur={() => onCompatibleModelNameCommit(compatibleModelName)}
              placeholder="qwen3.5:4b, deepseek-chat, glm-5.3, openrouter/free..."
              aria-label="Compatible endpoint model ID"
              className="st-input"
            />
            <p className="text-xs leading-relaxed text-neutral-600">
              Enter the exact model identifier expected by the endpoint.
            </p>
          </div>
          <div className="space-y-1.5">
            <label className="st-group-label" htmlFor="compatible-api-base-url">OpenAI-compatible API base URL</label>
            <input
              id="compatible-api-base-url"
              type="url"
              inputMode="url"
              autoComplete="url"
              value={compatibleBaseUrl}
              onChange={(event) => onCompatibleBaseUrlChange(event.target.value)}
              placeholder="https://api.example.com/v1"
              aria-label="OpenAI-compatible API base URL"
              className="st-input"
            />
            <p className="text-xs leading-relaxed text-neutral-600">
              Remote endpoints must use HTTPS; local loopback endpoints may use HTTP. Do not include the final /chat/completions path.
            </p>
          </div>
        </div>
      )}

      {portal.secretIds.map((secret) => {
        const field = CONNECTION_SECRET_FIELDS[secret]
        return (
          <SecretField
            key={secret}
            label={field.label}
            statusLabel={field.statusLabel}
            placeholder={field.placeholder}
            value={values[secret]}
            saved={statuses[secret]}
            show={showSecrets}
            onChange={(value) => onSecretChange(secret, value)}
            onDelete={() => onSecretChange(secret, '')}
            onToggle={onToggleSecrets}
          />
        )
      })}

      <p className="text-xs leading-relaxed text-neutral-600">
        Credentials are encrypted. Saved values stay hidden.
      </p>
    </div>
  )
}

function SecretField({
  label,
  statusLabel,
  placeholder,
  value,
  saved,
  show,
  onChange,
  onDelete,
  onToggle,
}: {
  label: string
  statusLabel: string
  placeholder: string
  value: string
  saved: boolean
  show: boolean
  onChange: (value: string) => void
  onDelete: () => void
  onToggle: () => void
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <span className="st-group-label">{label}</span>
        {saved ? (
          <Check size={11} className="text-emerald-400" strokeWidth={3} />
        ) : (
          <X size={11} className="text-red-400" strokeWidth={3} />
        )}
        <span className="sr-only">{statusLabel}: {saved ? 'yes' : 'no'}</span>
      </div>
      <div className="relative">
        <input
          type={show ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          aria-label={label}
          className="st-input st-input-with-action"
        />
        <button
          type="button"
          onClick={onToggle}
          className="st-btn st-btn-ghost st-btn-icon st-input-action"
          aria-label={show ? 'Hide secret' : 'Show secret'}
        >
          {show ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
      </div>
      {saved && !value && (
        <button
          type="button"
          onClick={onDelete}
          className="text-[11px] text-red-300 hover:text-red-200 hover:underline"
          aria-label={`Remove stored ${label}`}
        >
          Clear saved credential
        </button>
      )}
    </div>
  )
}
