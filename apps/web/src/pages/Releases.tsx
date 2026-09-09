import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useProjectApi,
  useProject,
  useDirty,
  useProjectSearchParams as useSearchParams,
} from "../project";
import { AgentSelector } from "../agent-selector";
import { Download, LockKeyhole, Plus } from "lucide-react";
import { ApiError, type Revision, type Mode, type Judge } from "../api";
import {
  DateLabel,
  EmptyState,
  ErrorState,
  JsonView,
  Loading,
  Notice,
  PageHeader,
  SectionHeading,
  ShortId,
  TextLink,
} from "../components";

function ReleaseInspector({ releaseId }: { releaseId: string }) {
  const api = useProjectApi();
  const { writeBlocked } = useProject();
  const [revision, setRevision] = useState<Revision | null>(null);
  const [mode, setMode] = useState<Mode | "">("");
  const [judge, setJudge] = useState<Judge>("none");
  const download = useMutation({
    mutationFn: () =>
      api.exportBundle(releaseId, revision!.id, mode as Mode, judge),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "goldenloop-release.zip";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  });
  const release = useQuery({
    queryKey: api.key("release", releaseId),
    queryFn: ({ signal }) => api.release(releaseId, signal),
  });
  if (release.isPending)
    return <Loading label="Loading immutable release..." />;
  if (release.isError)
    return <ErrorState error={release.error} retry={() => release.refetch()} />;
  return (
    <section className="panel">
      <SectionHeading
        title={release.data.name}
        detail="Immutable golden dataset snapshot"
      />
      <div className="detail-body">
        <form
          className="form-stack export-config"
          onSubmit={(e) => {
            e.preventDefault();
            if (
              revision &&
              mode &&
              revision.spec.modes?.includes(mode) &&
              !download.isPending
            )
              download.mutate();
          }}
        >
          <h3>Configure an executable test bundle</h3>
          <p>
            Explicitly choose a pinned agent revision and execution mode.
            Dataset-only download is not available. Archived revisions can still
            be exported.
          </p>
          <AgentSelector
            value={revision}
            purpose="export"
            disabled={download.isPending}
            onChange={(value) => {
              setRevision(value);
              setMode("");
              download.reset();
            }}
          />
          <label>
            Export execution mode
            <select
              required
              value={mode}
              disabled={!revision || download.isPending}
              onChange={(e) => setMode(e.target.value as Mode)}
            >
              <option value="">Select execution mode</option>
              {revision?.spec.modes?.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          <label>
            Export judge provider
            <select
              value={judge}
              disabled={download.isPending}
              onChange={(e) => setJudge(e.target.value as Judge)}
            >
              <option value="none">None / no judge provider</option>
              <option value="azure">
                Azure / requires server judge configuration
              </option>
            </select>
          </label>
          {(mode === "live" || judge === "azure") && (
            <Notice tone="warning">
              Export does not call a model. Running this bundle may incur usage
              costs. CI must supply its own credentials for the pinned
              connection and judge; no secrets are exported. Azure judge
              selection requires configured server metadata.
            </Notice>
          )}
          <button
            className="button align-start"
            disabled={!revision || !mode || download.isPending}
          >
            <Download size={16} />
            {download.isPending ? "Preparing bundle..." : "Export test bundle"}
          </button>
          {download.error && <ErrorState error={download.error} />}
          {download.isSuccess && (
            <Notice tone="success">Configured test bundle downloaded.</Notice>
          )}
        </form>
        <div className="release-meta">
          <div>
            <span className="small-label">RELEASE ID</span>
            <code>{release.data.id}</code>
          </div>
          <div>
            <span className="small-label">CONTENT HASH</span>
            <code>{release.data.content_hash}</code>
          </div>
          <div>
            <span className="small-label">CREATED</span>
            <DateLabel value={release.data.created_at} />
          </div>
          <div>
            <span className="small-label">PINNED CASES</span>
            <strong>{release.data.case_count}</strong>
          </div>
        </div>
        <Notice>
          Exports are downloaded directly from the backend as ZIP bundles. Case
          revisions are frozen; editing a candidate cannot rewrite this release.
        </Notice>
        {release.data.cases?.length ? (
          release.data.cases.map((item) => (
            <details
              key={`${item.id}-${item.revision}`}
              className="check-details"
            >
              <summary>
                <strong>{item.title}</strong>
                <span className="muted">
                  r{item.revision} / {item.checks.length} checks
                </span>
              </summary>
              <JsonView value={item} />
            </details>
          ))
        ) : (
          <EmptyState title="No cases returned">
            The API returned no case content for this release.
          </EmptyState>
        )}
        {!writeBlocked && (
          <TextLink to={`/runs?release=${releaseId}`}>
            Evaluate this release
          </TextLink>
        )}
      </div>
    </section>
  );
}

export default function Releases() {
  const api = useProjectApi();
  const { writeBlocked } = useProject();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const selected = params.get("release");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [expectedRevisions, setExpectedRevisions] = useState<
    Record<string, number>
  >({});
  const [confirmed, setConfirmed] = useState(false);
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const releases = useQuery({
    queryKey: api.key("releases"),
    queryFn: ({ signal }) => api.releases(signal),
  });
  const cases = useQuery({
    queryKey: api.key("cases"),
    queryFn: ({ signal }) => api.cases(signal),
    enabled: creating,
  });
  const eligible =
    cases.data?.filter(
      (record) => record.status === "approved" && record.case.id,
    ) ?? [];
  const selectedIds = Object.keys(expectedRevisions);
  const staleIds = selectedIds.filter(
    (id) =>
      !eligible.some(
        (record) =>
          record.case.id === id &&
          record.case.revision === expectedRevisions[id],
      ),
  );
  const create = useMutation({
    mutationFn: () =>
      api.createRelease(name.trim(), selectedIds, expectedRevisions),
    onError: async (error) => {
      if (error instanceof ApiError && error.status === 409) {
        setConflict(true);
        setExpectedRevisions({});
        setConfirmed(false);
        await Promise.all([
          client.invalidateQueries({ queryKey: api.key("cases") }),
          client.invalidateQueries({ queryKey: api.key("case") }),
          client.invalidateQueries({ queryKey: api.key("releases") }),
        ]);
      }
    },
    onSuccess: (release) => {
      client.invalidateQueries({ queryKey: api.key("releases") });
      client.invalidateQueries({ queryKey: api.key("summary") });
      clearDirty();
      setParams({ release: release.id });
      setCreating(false);
      setName("");
      setExpectedRevisions({});
      setConfirmed(false);
      setConflict(false);
      setNotice(
        `Release "${release.name}" created with ${release.case_count} cases.`,
      );
    },
  });
  const clearDirty = useDirty(
    creating && (!!name || selectedIds.length > 0 || confirmed),
  );
  return (
    <>
      <PageHeader
        eyebrow="04 / FREEZE THE STANDARD"
        title="A reference you can return to."
        description="Publish reviewed cases as immutable golden releases. Keep evaluations reproducible and export self-contained test bundles."
        action={
          <button
            className="button"
            disabled={writeBlocked}
            onClick={() => setCreating((value) => !value)}
          >
            <Plus size={16} />
            Create release
          </button>
        }
      />
      {notice && <Notice tone="success">{notice}</Notice>}
      {creating && (
        <section className="panel">
          <SectionHeading
            title="Publish a golden release"
            detail="Only the latest approved cases are eligible. The backend validates approval again at publication."
          />
          <form
            className="form-stack"
            onSubmit={(event) => {
              event.preventDefault();
              if (
                writeBlocked ||
                !confirmed ||
                !selectedIds.length ||
                staleIds.length ||
                cases.isFetching ||
                cases.isError ||
                create.isPending
              )
                return;
              create.mutate();
            }}
          >
            <label>
              Release name
              <input
                required
                disabled={create.isPending}
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="A meaningful name for this evaluation baseline"
              />
            </label>
            {cases.isPending ? (
              <Loading label="Loading approved cases..." />
            ) : cases.isError ? (
              <ErrorState error={cases.error} retry={() => cases.refetch()} />
            ) : !eligible.length ? (
              <EmptyState
                title="No approved cases available"
                action={<TextLink to="/candidates">Go to review desk</TextLink>}
              >
                A candidate must be explicitly approved before it can join a
                golden release.
              </EmptyState>
            ) : (
              <fieldset
                className="case-selection"
                disabled={create.isPending || cases.isFetching}
              >
                <legend>Select approved case revisions</legend>
                <label className="checkbox-label select-all">
                  <input
                    type="checkbox"
                    checked={
                      selectedIds.length === eligible.length && !staleIds.length
                    }
                    onChange={(event) => {
                      setConfirmed(false);
                      setExpectedRevisions(
                        event.target.checked
                          ? Object.fromEntries(
                              eligible.map((record) => [
                                record.case.id!,
                                record.case.revision,
                              ]),
                            )
                          : {},
                      );
                    }}
                  />
                  Select all eligible cases ({eligible.length})
                </label>
                {eligible.map((record) => (
                  <label
                    className="checkbox-label selection-row"
                    key={record.case.id}
                  >
                    <input
                      type="checkbox"
                      checked={
                        expectedRevisions[record.case.id!] ===
                        record.case.revision
                      }
                      onChange={(event) => {
                        setConfirmed(false);
                        setExpectedRevisions((previous) =>
                          event.target.checked
                            ? {
                                ...previous,
                                [record.case.id!]: record.case.revision,
                              }
                            : Object.fromEntries(
                                Object.entries(previous).filter(
                                  ([id]) => id !== record.case.id,
                                ),
                              ),
                        );
                      }}
                    />
                    <span>
                      <strong>{record.case.title}</strong>
                      <small>
                        Revision {record.case.revision} /{" "}
                        {record.case.checks.length} checks
                      </small>
                    </span>
                  </label>
                ))}
              </fieldset>
            )}
            {staleIds.length > 0 && (
              <Notice tone="warning">
                Selected revisions changed or are no longer approved:{" "}
                {staleIds
                  .map((id) => `${id} / revision ${expectedRevisions[id]}`)
                  .join(", ")}
                . No newer revision has been selected automatically. Review the
                current cases and select again.
                <button
                  className="button small secondary"
                  type="button"
                  disabled={create.isPending}
                  onClick={() => {
                    setExpectedRevisions({});
                    setConfirmed(false);
                  }}
                >
                  Clear selection
                </button>
              </Notice>
            )}
            {conflict && (
              <Notice tone="warning">
                Publication conflict. Selections and confirmation were cleared;
                the latest cases are being reloaded. Review and reselect the
                revisions before publishing again. Nothing was automatically
                retried or replaced.
              </Notice>
            )}
            <label className="checkbox-label">
              <input
                type="checkbox"
                required
                disabled={
                  writeBlocked ||
                  create.isPending ||
                  !!staleIds.length ||
                  cases.isFetching ||
                  !selectedIds.length
                }
                checked={confirmed && !staleIds.length}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              Publish an immutable release of these approved revisions.
            </label>
            {create.error && <ErrorState error={create.error} />}
            <div className="form-actions">
              <button
                className="button"
                disabled={
                  create.isPending ||
                  !name.trim() ||
                  !selectedIds.length ||
                  !!staleIds.length ||
                  !confirmed ||
                  cases.isError ||
                  cases.isFetching
                }
              >
                <LockKeyhole size={15} />
                {create.isPending
                  ? "Publishing..."
                  : `Publish ${selectedIds.length} selected cases`}
              </button>
              <button
                className="button secondary"
                type="button"
                onClick={() => setCreating(false)}
              >
                Cancel
              </button>
            </div>
          </form>
        </section>
      )}
      <section className="panel">
        <SectionHeading
          title="Golden releases"
          detail="Every snapshot has its own content hash and pinned case revisions."
        />
        {releases.isPending ? (
          <Loading />
        ) : releases.isError ? (
          <ErrorState error={releases.error} retry={() => releases.refetch()} />
        ) : !releases.data.length ? (
          <EmptyState
            title="Set your first quality baseline"
            action={<TextLink to="/candidates">Review candidates</TextLink>}
          >
            Approve the cases you trust, then publish them together as a golden
            release.
          </EmptyState>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Release</th>
                  <th>Cases</th>
                  <th>Content hash</th>
                  <th>Created</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {releases.data.map((release) => (
                  <tr
                    key={release.id}
                    className={selected === release.id ? "selected-row" : ""}
                  >
                    <td>
                      <strong>{release.name}</strong>
                      <div className="cell-sub">
                        <ShortId value={release.id} />
                      </div>
                    </td>
                    <td>{release.case_count}</td>
                    <td>
                      <ShortId value={release.content_hash} />
                    </td>
                    <td>
                      <DateLabel value={release.created_at} />
                    </td>
                    <td>
                      <div className="row-actions">
                        <button
                          className="button small secondary"
                          onClick={() => setParams({ release: release.id })}
                        >
                          Inspect
                          <span className="sr-only"> {release.name}</span>
                        </button>
                        <button
                          className="icon-button"
                          onClick={() => setParams({ release: release.id })}
                          aria-label={`Configure export for ${release.name}`}
                        >
                          <Download size={17} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {selected && <ReleaseInspector key={selected} releaseId={selected} />}
    </>
  );
}
