import { useState } from "react";
import { ActionButton } from "./ActionButton";

type QuickRunModalProps = {
  deploymentName: string;
  defaultParameters?: Record<string, unknown>;
  onClose: () => void;
  onSubmit: (payload: { parameters: Record<string, unknown>; idempotency_key?: string }) => void;
  isPending?: boolean;
};

export function QuickRunModal({
  deploymentName,
  defaultParameters = {},
  onClose,
  onSubmit,
  isPending
}: QuickRunModalProps) {
  const [parametersJson, setParametersJson] = useState(JSON.stringify(defaultParameters, null, 2));
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = () => {
    try {
      const parameters = parametersJson.trim() ? (JSON.parse(parametersJson) as Record<string, unknown>) : {};
      onSubmit({
        parameters,
        idempotency_key: idempotencyKey.trim() || undefined
      });
      setError(null);
    } catch {
      setError("Parameters must be valid JSON.");
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal">
        <h3>Quick Run: {deploymentName}</h3>
        <label className="field-label">
          Parameters (JSON)
          <textarea
            className="field-input mono-list"
            rows={8}
            value={parametersJson}
            onChange={(e) => setParametersJson(e.target.value)}
          />
        </label>
        <label className="field-label">
          Idempotency key (optional)
          <input
            className="field-input"
            value={idempotencyKey}
            onChange={(e) => setIdempotencyKey(e.target.value)}
          />
        </label>
        {error ? <p className="form-error">{error}</p> : null}
        <div className="modal-actions">
          <ActionButton onClick={onClose} disabled={isPending}>
            Cancel
          </ActionButton>
          <ActionButton variant="primary" onClick={handleSubmit} disabled={isPending}>
            {isPending ? "Starting..." : "Run"}
          </ActionButton>
        </div>
      </div>
    </div>
  );
}
