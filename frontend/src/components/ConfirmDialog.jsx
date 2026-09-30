export default function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel = "Conferma",
  cancelLabel = "Annulla",
  danger = false,
  loading = false,
  onConfirm,
  onCancel,
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/40 p-4">
      <div className="w-full max-w-md rounded-md border border-slate-200 bg-white shadow-xl">
        <div className="border-b border-slate-200 px-5 py-4">
          <h2 className="font-heading text-lg font-black text-slate-900">{title}</h2>
        </div>
        <div className="px-5 py-4 text-sm text-slate-700 leading-relaxed">{children}</div>
        <div className="flex justify-end gap-2 border-t border-slate-200 px-5 py-4">
          <button onClick={onCancel} disabled={loading} className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-50 disabled:opacity-40">
            {cancelLabel}
          </button>
          <button onClick={onConfirm} disabled={loading} className={`rounded-md px-4 py-2 text-sm font-bold text-white disabled:opacity-40 ${danger ? "bg-red-600 hover:bg-red-700" : "bg-slate-900 hover:bg-slate-800"}`}>
            {loading ? "Attendere..." : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
