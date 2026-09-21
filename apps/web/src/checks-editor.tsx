import { useRef, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, type CaseRecord, type CheckKind } from "./api";
import { checkKinds } from "./case-form";
import { buildCheck, checkDraft, checkLabels, newCheck, supportsStructured, type CheckDraft } from "./check-form";
import { ErrorState, Notice, SectionHeading } from "./components";
import { confirmDiscard, useDirty, useInstanceGuard, useProject, useProjectApi } from "./project";

export function ChecksEditor({ record, onSaved, onClose }: {
  record: CaseRecord;
  onSaved: (record: CaseRecord) => void;
  onClose: () => void;
}) {
  const api = useProjectApi();
  const { writeBlocked, dirty } = useProject();
  const captureInstance = useInstanceGuard();
  const client = useQueryClient();
  const [drafts, setDrafts] = useState(() => record.case.checks.map(checkDraft));
  const initial = useRef(JSON.stringify(drafts));
  const nextKey = useRef(drafts.length);
  const [kind, setKind] = useState<CheckKind>("content_contains");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [conflict, setConflict] = useState(false);
  const clearDirty = useDirty(!!reason || JSON.stringify(drafts) !== initial.current);
  const save = useMutation({
    mutationFn: ({ value, isCurrent }: { value: CaseRecord["case"]; isCurrent: () => boolean }) => {
      if (writeBlocked || !isCurrent()) throw new Error("Project writes are disabled.");
      return api.updateCase(record.case.id!, value, record.case.revision, reason.trim());
    },
    onSuccess: (saved, { isCurrent }) => {
      client.setQueryData(api.key("case", record.case.id), saved);
      client.invalidateQueries({ queryKey: api.key("cases") });
      client.invalidateQueries({ queryKey: api.key("case", record.case.id) });
      client.invalidateQueries({ queryKey: api.key("summary") });
      if (isCurrent()) {
        clearDirty();
        onSaved(saved);
      }
    },
    onError: (error, { isCurrent }) => {
      if (isCurrent() && error instanceof ApiError && error.status === 409) {
        setConflict(true);
        client.invalidateQueries({ queryKey: api.key("case", record.case.id) });
        client.invalidateQueries({ queryKey: api.key("cases") });
      }
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    if (writeBlocked || save.isPending || conflict) return;
    setError(null);
    try {
      if (!reason.trim()) throw new Error("A revision reason is required.");
      const checks = drafts.map((draft, index) => {
        try { return buildCheck(draft, record.case.turns.length); }
        catch (error) { throw new Error(`Check ${index + 1}: ${(error as Error).message}`); }
      });
      save.mutate({ value: { ...record.case, checks }, isCurrent: captureInstance() });
    } catch (error) { setError(error as Error); }
  }
  function close() {
    if (!save.isPending && confirmDiscard(dirty)) {
      clearDirty();
      onClose();
    }
  }
  const update = (key: number, change: Partial<CheckDraft>) =>
    setDrafts((items) => items.map((item) => item.key === key ? { ...item, ...change } : item));
  return (
    <section className="panel editor-panel">
      <SectionHeading title={`Edit checks / revision ${record.case.revision}`} detail={record.case.title} />
      <form className="form-stack" onSubmit={submit} noValidate>
        <Notice>
          Author expectations deliberately. Answers and observed tool arguments are not copied into checks.
          Saving creates a new candidate revision and preserves all other case fields and provenance.
        </Notice>
        <fieldset disabled={writeBlocked || save.isPending} className="form-stack">
          <legend>Authored checks</legend>
          {drafts.map((draft, index) => {
            const check = draft.check;
            const config = check.config;
            const configField = (name: string, value: unknown) => update(draft.key, { check: { ...check, config: { ...config, [name]: value } } });
            return (
              <fieldset key={draft.key} className="form-stack">
                <legend>Check {index + 1}: {checkLabels[check.kind as CheckKind] ?? check.kind}</legend>
                {check.id && <span className="field-hint">Check ID: {check.id}</span>}
                <div className="form-row">
                  <label className="checkbox-label">
                    <input type="checkbox" checked={check.required} onChange={(event) => update(draft.key, { check: { ...check, required: event.target.checked } })} />
                    Required
                  </label>
                  <label>Applies to
                    <select value={check.turn === null ? "all" : String(check.turn)} onChange={(event) => update(draft.key, { check: { ...check, turn: event.target.value === "all" ? null : Number(event.target.value) } })}>
                      {record.case.turns.map((_, turn) => <option key={turn} value={turn}>Turn {turn + 1}</option>)}
                      <option value="all">All turns</option>
                    </select>
                  </label>
                </div>
                {draft.advanced ? (
                  <>
                    <label>Check configuration JSON
                      <textarea className="code-editor" rows={8} spellCheck={false} value={draft.config} onChange={(event) => update(draft.key, { config: event.target.value })} />
                    </label>
                    <span className="field-hint">The complete configuration is retained. The server validates supported fields and evaluator settings.</span>
                  </>
                ) : (
                  <>
                    {(check.kind.startsWith("content_") || check.kind === "exact_match") && <label>Expected content
                      <textarea rows={3} value={String(config.value)} onChange={(event) => configField("value", event.target.value)} />
                    </label>}
                    {check.kind.startsWith("tool_") && <label>Tool name
                      <input list="check-tool-suggestions" value={String(config.tool)} onChange={(event) => configField("tool", event.target.value)} />
                      <span className="field-hint">Exact name. VaR suggestions are examples, not inferred expectations or evidence that a tool was called.</span>
                    </label>}
                    {check.kind === "tool_required" && <label>Minimum calls
                      <input type="number" min={1} step={1} value={draft.minimum} onChange={(event) => update(draft.key, { minimum: event.target.value })} />
                    </label>}
                    {check.kind === "tool_arguments" && <>
                      <label>Argument path
                        <input value={String(config.path)} onChange={(event) => configField("path", event.target.value)} />
                        <span className="field-hint">Dot path or JSON pointer; empty checks the entire arguments object.</span>
                      </label>
                      <label>Argument equals (JSON value)
                        <textarea rows={2} value={draft.argument} onChange={(event) => update(draft.key, { argument: event.target.value })} />
                        <span className="field-hint">For example, a quoted string, number, object, array, boolean, or null.</span>
                      </label>
                    </>}
                    {check.kind === "judge" && <>
                      <label>Judge rubric
                        <textarea rows={3} value={String(config.rubric)} onChange={(event) => configField("rubric", event.target.value)} />
                      </label>
                      <label>Judge threshold
                        <input type="number" min={0} max={1} step="any" value={draft.threshold} onChange={(event) => update(draft.key, { threshold: event.target.value })} />
                      </label>
                      <span className="field-hint">Requires a configured backend judge; no judge verdict is inferred here.</span>
                    </>}
                  </>
                )}
                <div className="form-actions">
                  <button type="button" className="button secondary" onClick={() => {
                    try {
                      const value = buildCheck(draft, record.case.turns.length);
                      if (draft.advanced && !supportsStructured(value)) throw new Error("This configuration requires JSON to preserve all settings.");
                      update(draft.key, { ...checkDraft(value, draft.key), advanced: !draft.advanced });
                      setError(null);
                    } catch (error) { setError(error as Error); }
                  }}>{draft.advanced ? "Use structured fields" : "Edit configuration JSON"}</button>
                  <button type="button" className="quiet-button" onClick={() => setDrafts((items) => items.filter((item) => item.key !== draft.key))}>Remove check {index + 1}</button>
                </div>
              </fieldset>
            );
          })}
          <datalist id="check-tool-suggestions">
            <option value="similar_occurrences" /><option value="risk_assessment" /><option value="commercial_outlook" />
          </datalist>
          <div className="form-row">
            <label>New check kind
              <select value={kind} onChange={(event) => setKind(event.target.value as CheckKind)}>
                {checkKinds.map((kind) => <option key={kind} value={kind}>{checkLabels[kind]}</option>)}
              </select>
            </label>
            <button type="button" className="button secondary align-start" onClick={() => setDrafts((items) => [...items, checkDraft(newCheck(kind), nextKey.current++)])}>Add check</button>
          </div>
          <label>Revision reason
            <input required value={reason} onChange={(event) => setReason(event.target.value)} />
          </label>
        </fieldset>
        {conflict && <Notice tone="warning">Revision conflict. Your draft is retained. Cancel and reopen the latest revision to reconcile changes before saving again.</Notice>}
        {(error || save.error) && <ErrorState error={error || save.error} />}
        <div className="form-actions">
          <button className="button" disabled={writeBlocked || save.isPending || conflict}>{save.isPending ? "Saving..." : "Save new candidate revision"}</button>
          <button type="button" className="button secondary" disabled={save.isPending} onClick={close}>Cancel</button>
          <span className="field-hint">Saving does not approve this case.</span>
        </div>
      </form>
    </section>
  );
}
