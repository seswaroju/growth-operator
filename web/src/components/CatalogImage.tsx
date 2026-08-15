// Product photograph for one catalog item (DEMO-UX-1).
//
// Upload by drag-and-drop or file picker, preview, replace, remove. One primary image per item for
// the pilot.
//
// The image endpoints are authenticated, so a plain <img src> cannot load them — the browser sends
// no Authorization header on an image request. The bytes are fetched and handed to the element as
// an object URL, which is revoked on unmount; without that the blob would stay alive for the life
// of the page.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { deleteCatalogImage, fetchCatalogImageObjectUrl, uploadCatalogImage } from "../api";
import { useAuth } from "../auth";
import { buttonClasses } from "../lib/ui";

/** Matches the server's allow-list. The server re-checks by decoding — this only saves a merchant
 *  a round trip for an obviously wrong file. */
const ACCEPT = "image/jpeg,image/png,image/webp";

export function CatalogImage({ itemId, hasImage }: { itemId: string; hasImage: boolean }) {
  const { token } = useAuth();
  const qc = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token || !hasImage) return;
    const url = await fetchCatalogImageObjectUrl(token, itemId, "thumbnail");
    setPreview(url);
  }, [token, itemId, hasImage]);

  useEffect(() => {
    void load();
  }, [load]);

  // Revoke on unmount and whenever the URL is replaced — an object URL is a live reference to
  // decoded bytes and is not garbage collected on its own.
  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  const upload = useMutation({
    mutationFn: (file: File) => uploadCatalogImage(token!, itemId, file),
    onSuccess: async () => {
      setError(null);
      if (preview) URL.revokeObjectURL(preview);
      setPreview(null);
      const url = await fetchCatalogImageObjectUrl(token!, itemId, "thumbnail");
      setPreview(url);
      void qc.invalidateQueries({ queryKey: ["catalog"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: () => deleteCatalogImage(token!, itemId),
    onSuccess: () => {
      if (preview) URL.revokeObjectURL(preview);
      setPreview(null);
      setError(null);
      void qc.invalidateQueries({ queryKey: ["catalog"] });
    },
  });

  function accept(files: FileList | null) {
    const file = files?.[0];
    if (file) upload.mutate(file);
  }

  return (
    <div className="flex items-start gap-3">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); accept(e.dataTransfer.files); }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        aria-label={preview ? "Replace product photo" : "Upload product photo"}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") inputRef.current?.click(); }}
        className={`grid h-24 w-24 shrink-0 cursor-pointer place-items-center overflow-hidden
          rounded-xl border border-dashed text-center transition
          ${dragging ? "border-accent-ink bg-accent-soft" : "border-line bg-line-2/40"}`}
      >
        {upload.isPending ? (
          <span className="text-[11px] text-muted">Uploading…</span>
        ) : preview ? (
          <img src={preview} alt="Product" className="h-full w-full object-contain" />
        ) : (
          <span className="px-2 text-[11px] leading-tight text-muted">
            Drop a photo<br />or click
          </span>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="hidden"
          onChange={(e) => { accept(e.target.files); e.target.value = ""; }}
        />
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={upload.isPending}
            className={buttonClasses("ghost", "sm")}
          >
            {preview ? "Replace" : "Upload"}
          </button>
          {preview && (
            <button
              type="button"
              onClick={() => remove.mutate()}
              disabled={remove.isPending}
              className={buttonClasses("ghost", "sm")}
            >
              {remove.isPending ? "Removing…" : "Remove"}
            </button>
          )}
        </div>
        <p className="text-[11px] leading-relaxed text-muted">
          JPEG, PNG or WebP · up to 10 MB. Resized for the web automatically — nothing is cropped.
        </p>
        {error && <p className="text-[11px] text-danger">{error}</p>}
      </div>
    </div>
  );
}
