import { useState } from "react";
import { ActionButton } from "./ActionButton";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";

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
    <Dialog open onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>Quick Run: {deploymentName}</DialogTitle>
        </DialogHeader>
        <label className="mb-3 block text-sm">
          Parameters (JSON)
          <textarea
            className="field-input mono-list mt-1 w-full rounded-md border border-border bg-surface p-2 font-mono text-sm"
            rows={8}
            value={parametersJson}
            onChange={(e) => setParametersJson(e.target.value)}
          />
        </label>
        <label className="mb-3 block text-sm">
          Idempotency key (optional)
          <Input className="mt-1" value={idempotencyKey} onChange={(e) => setIdempotencyKey(e.target.value)} />
        </label>
        {error ? <p className="form-error text-sm text-warning">{error}</p> : null}
        <div className="mt-3 flex justify-end gap-2">
          <ActionButton onClick={onClose} disabled={isPending}>
            Cancel
          </ActionButton>
          <ActionButton variant="primary" onClick={handleSubmit} disabled={isPending}>
            {isPending ? "Starting..." : "Run"}
          </ActionButton>
        </div>
      </DialogContent>
    </Dialog>
  );
}
