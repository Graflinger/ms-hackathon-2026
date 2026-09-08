import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, FileSpreadsheet, Upload } from "lucide-react";
import { api, type ImportPreview } from "../api";
import {
  EmptyState,
  ErrorState,
  Notice,
  PageHeader,
  SectionHeading,
  TextLink,
} from "../components";

const mappingFields = [
  { key: "title", label: "Case title (optional)", required: false },
  { key: "user", label: "User message", required: true },
  { key: "reference_answer", label: "Reference answer", required: false },
  { key: "tags", label: "Tags", required: false },
  { key: "context", label: "Context", required: false },
];
export function initialMapping(columns: string[]): Record<string, string> {
  return Object.fromEntries(
    mappingFields.flatMap((field) => {
      const column = columns.find(
        (column) => column.trim().toLowerCase() === field.key,
      );
      return column ? [[field.key, column]] : [];
    }),
  );
}
function displayValue(value: unknown): string {
  return value == null
    ? ""
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
}

export default function ImportPage() {
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [sheet, setSheet] = useState("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [duplicates, setDuplicates] = useState<"new" | "reject">("reject");
  const [confirmed, setConfirmed] = useState(false);
  const [extraField, setExtraField] = useState("");
  const [extraColumn, setExtraColumn] = useState("");
  const upload = useMutation({
    mutationFn: () => api.preview(file!, sheet || undefined),
    onSuccess: (data) => {
      setPreview(data);
      setMapping(initialMapping(data.columns));
      setConfirmed(false);
      commit.reset();
    },
  });
  const commit = useMutation({
    mutationFn: () =>
      api.commitImport(
        preview!.id,
        Object.fromEntries(
          Object.entries(mapping).filter(([, value]) => value),
        ),
        duplicates,
        sheet || undefined,
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["cases"] });
      client.invalidateQueries({ queryKey: ["summary"] });
    },
  });
  function submitPreview(event: FormEvent) {
    event.preventDefault();
    setPreview(null);
    upload.mutate();
  }
  const flagged = preview?.rows.filter((row) => row.errors.length).length ?? 0;
  return (
    <>
      <PageHeader
        eyebrow="01 / COLLECT THE EVIDENCE"
        title="A good dataset starts here."
        description="Bring Excel or CSV scenarios into the loop. Inspect the source, map fields, and import candidates for human review."
      />
      <div className="step-strip">
        <span className={!preview ? "active" : ""}>
          <b>01</b>Upload & inspect
        </span>
        <ArrowRight size={16} />
        <span className={preview && !commit.isSuccess ? "active" : ""}>
          <b>02</b>Map columns
        </span>
        <ArrowRight size={16} />
        <span className={commit.isSuccess ? "active" : ""}>
          <b>03</b>Commit candidates
        </span>
      </div>
      <section className="panel">
        <SectionHeading
          title="Source file"
          detail="Synthetic data only for this demo. Remove sensitive data before upload."
        />
        <form className="import-upload" onSubmit={submitPreview}>
          <div className="file-zone">
            <FileSpreadsheet size={32} strokeWidth={1.3} />
            <div>
              <label htmlFor="source-file">
                Choose an Excel (.xlsx) or CSV file
              </label>
              <p>
                The original rows are previewed before anything is committed.
              </p>
              <input
                id="source-file"
                type="file"
                accept=".csv,.xlsx"
                required
                disabled={upload.isPending || commit.isPending}
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                  setPreview(null);
                  setSheet("");
                  setConfirmed(false);
                  upload.reset();
                  commit.reset();
                }}
              />
            </div>
          </div>
          <div className="form-row">
            <label>
              Worksheet{" "}
              <span className="optional">
                optional; blank uses backend default
              </span>
              <input
                value={sheet}
                disabled={upload.isPending || commit.isPending}
                list="sheet-options"
                placeholder="Worksheet name"
                onChange={(event) => {
                  setSheet(event.target.value);
                  setPreview(null);
                  setConfirmed(false);
                  commit.reset();
                }}
              />
              <datalist id="sheet-options">
                {upload.data?.sheets.map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
            </label>
            <button
              className="button"
              disabled={!file || upload.isPending || commit.isPending}
            >
              <Upload size={16} />
              {upload.isPending ? "Reading file..." : "Preview file"}
            </button>
          </div>
          {upload.error && <ErrorState error={upload.error} />}
        </form>
      </section>
      {!preview && !upload.isPending && (
        <EmptyState title="Inspect before you import">
          Choose a source file to see its columns, row values, and validation
          messages.
        </EmptyState>
      )}
      {preview && (
        <>
          <section className="panel">
            <SectionHeading
              title="Inspect the source"
              detail={`${preview.rows.length} preview rows / ${preview.columns.length} columns / ${flagged} flagged rows`}
            />
            <div className="detail-body">
              {preview.sheets.length > 0 && (
                <p className="muted">
                  Available worksheets: {preview.sheets.join(", ")}. Change the
                  worksheet above and preview again to switch sheets.
                </p>
              )}
              {preview.errors.length > 0 && (
                <Notice tone="warning">
                  <strong>File validation messages</strong>
                  <ul>
                    {preview.errors.map((error, index) => (
                      <li key={index}>{error}</li>
                    ))}
                  </ul>
                </Notice>
              )}
              {flagged > 0 && (
                <Notice tone="warning">
                  Flagged rows need attention. The backend determines which rows
                  can be committed; review any returned errors after import.
                </Notice>
              )}
            </div>
            {preview.rows.length ? (
              <div className="table-scroll preview-table">
                <table>
                  <thead>
                    <tr>
                      <th>Row</th>
                      {preview.columns.map((column) => (
                        <th key={column}>{column}</th>
                      ))}
                      <th>Validation</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows.map((row, index) => (
                      <tr
                        key={`${row.row}-${index}`}
                        className={row.errors.length ? "flagged-row" : ""}
                      >
                        <td className="mono">{row.row}</td>
                        {preview.columns.map((column) => (
                          <td
                            key={column}
                            title={displayValue(row.values[column])}
                          >
                            {displayValue(row.values[column])}
                          </td>
                        ))}
                        <td>
                          {row.errors.length ? (
                            row.errors.map((error, index) => (
                              <div className="row-error" key={index}>
                                {error}
                              </div>
                            ))
                          ) : (
                            <span className="muted">No preview errors</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="No rows in this preview">
                Select another worksheet or upload a file with data rows.
              </EmptyState>
            )}
          </section>
          <section className="panel">
            <SectionHeading
              title="Map columns to a candidate"
              detail="Mappings are sent as canonical field names to uploaded column names. Nothing is approved automatically."
            />
            <form
              className="form-stack"
              onSubmit={(event) => {
                event.preventDefault();
                commit.mutate();
              }}
            >
              <div className="mapping-grid">
                <span className="small-label">CANONICAL FIELD</span>
                <span className="small-label">UPLOADED COLUMN</span>
                {mappingFields.map((field) => (
                  <div className="mapping-row" key={field.key}>
                    <label htmlFor={`map-${field.key}`}>
                      {field.label}
                      {field.required && (
                        <span className="required-mark"> *</span>
                      )}
                      <code>{field.key}</code>
                    </label>
                    <select
                      id={`map-${field.key}`}
                      required={field.required}
                      disabled={commit.isPending || commit.isSuccess}
                      value={mapping[field.key] || ""}
                      onChange={(event) =>
                        setMapping((previous) => ({
                          ...previous,
                          [field.key]: event.target.value,
                        }))
                      }
                    >
                      <option value="">
                        {field.required ? "Select a column" : "Do not import"}
                      </option>
                      {preview.columns.map((column) => (
                        <option key={column} value={column}>
                          {column}
                        </option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>
              <details className="help-details">
                <summary>Additional canonical field mapping</summary>
                <p>
                  For backend-supported import fields beyond the practical
                  mapping above. Use the canonical field key exactly;
                  unsupported fields may be rejected by the API.
                </p>
                <div className="form-row">
                  <label>
                    Canonical field
                    <input
                      value={extraField}
                      onChange={(event) => setExtraField(event.target.value)}
                    />
                  </label>
                  <label>
                    Uploaded column
                    <select
                      value={extraColumn}
                      onChange={(event) => setExtraColumn(event.target.value)}
                    >
                      <option value="">Select a column</option>
                      {preview.columns.map((column) => (
                        <option key={column}>{column}</option>
                      ))}
                    </select>
                  </label>
                  <button
                    className="button secondary"
                    type="button"
                    disabled={
                      !extraField.trim() ||
                      !extraColumn ||
                      commit.isPending ||
                      commit.isSuccess
                    }
                    onClick={() => {
                      setMapping((previous) => ({
                        ...previous,
                        [extraField.trim()]: extraColumn,
                      }));
                      setExtraField("");
                      setExtraColumn("");
                    }}
                  >
                    Add mapping
                  </button>
                </div>
                {Object.entries(mapping)
                  .filter(
                    ([key]) =>
                      !mappingFields.some((field) => field.key === key),
                  )
                  .map(([key, value]) => (
                    <div className="extra-mapping" key={key}>
                      <code>{key}</code>
                      <span>{value}</span>
                      <button
                        className="button small secondary"
                        type="button"
                        disabled={commit.isPending || commit.isSuccess}
                        onClick={() =>
                          setMapping((previous) =>
                            Object.fromEntries(
                              Object.entries(previous).filter(
                                ([field]) => field !== key,
                              ),
                            ),
                          )
                        }
                      >
                        Remove {key}
                      </button>
                    </div>
                  ))}
              </details>
              <label>
                Duplicate policy
                <select
                  value={duplicates}
                  disabled={commit.isPending || commit.isSuccess}
                  onChange={(event) =>
                    setDuplicates(event.target.value as typeof duplicates)
                  }
                >
                  <option value="reject">Reject duplicates</option>
                  <option value="new">Create as new cases</option>
                </select>
              </label>
              <p className="field-hint">
                Without a title mapping, the backend derives the case title from
                the user message. Resolve preview errors in the source file and
                upload again before committing.
              </p>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  required
                  checked={confirmed}
                  disabled={commit.isPending || commit.isSuccess}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                I reviewed the preview and mapping. Import as unapproved
                candidates.
              </label>
              {commit.error && <ErrorState error={commit.error} />}
              <div className="form-actions">
                <button
                  className="button"
                  disabled={
                    commit.isPending ||
                    commit.isSuccess ||
                    !confirmed ||
                    !preview.rows.length ||
                    !!preview.errors.length ||
                    flagged > 0 ||
                    !mapping.user
                  }
                >
                  {commit.isPending
                    ? "Importing..."
                    : commit.isSuccess
                      ? "Import committed"
                      : "Commit import"}
                  <ArrowRight size={16} />
                </button>
              </div>
            </form>
          </section>
        </>
      )}
      {commit.data && (
        <section className="panel">
          <SectionHeading title="Import receipt" />
          <div className="detail-body">
            <Notice tone={commit.data.errors.length ? "warning" : "success"}>
              {commit.data.cases.length} candidates created.{" "}
              {commit.data.errors.length} import errors returned. No cases were
              automatically approved.
            </Notice>
            {commit.data.errors.length > 0 && (
              <ul className="error-list">
                {commit.data.errors.map((error, index) => (
                  <li key={index}>{error}</li>
                ))}
              </ul>
            )}
            {commit.data.cases.length > 0 && (
              <>
                <ul className="receipt-list">
                  {commit.data.cases.map((record, index) => (
                    <li key={record.case.id || index}>
                      {record.case.title}
                      <span className="muted">
                        revision {record.case.revision}
                      </span>
                    </li>
                  ))}
                </ul>
                <TextLink to="/candidates">
                  Continue to the review desk
                </TextLink>
              </>
            )}
          </div>
        </section>
      )}
    </>
  );
}
