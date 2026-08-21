import React from "react";
import axios from "axios";
import { ChevronLeft, HelpCircle, Mail, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "./ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "./ui/alert-dialog";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;
const SUPPORT_EMAIL_TO = "info@pontis.one";

const FAQ_ITEMS = [
  {
    q: "How do I update my profile?",
    a: "Open Profile in the dashboard to edit details, replace your resume, and manage certificates or your profile photo.",
  },
  {
    q: "Why am I seeing fewer job matches?",
    a: "Matches depend on your profile strength and the preferences Eve has learned from your resume, chat, and voice intake.",
  },
  {
    q: "Can I pause job matching?",
    a: "Yes. Use the Open to opportunities toggle in your profile to temporarily stop being shown new opportunities.",
  },
  {
    q: "How do I continue voice intake?",
    a: "Open Chat with Eve and continue the saved voice intake flow from the dashboard.",
  },
];

function AuthHeader(candidateToken) {
  return candidateToken ? { Authorization: `Bearer ${candidateToken}` } : {};
}

function ViewShell({ title, description, onBack, children }) {
  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3">
        {onBack ? (
          <button
            type="button"
            onClick={onBack}
            className="mt-0.5 inline-flex h-8 w-8 items-center justify-center rounded-full bg-black/[0.04] text-[#4A4A48] hover:bg-black/[0.08] transition-colors"
            aria-label="Go back"
          >
            <ChevronLeft className="h-4 w-4" strokeWidth={2} />
          </button>
        ) : null}
        <div className="min-w-0">
          <h3 className="text-[18px] font-medium tracking-tight text-[#1F1F1F]">
            {title}
          </h3>
          <p className="mt-1 text-[13px] leading-relaxed text-[#6B6B69]">
            {description}
          </p>
        </div>
      </div>
      <div>{children}</div>
    </div>
  );
}

export default function CandidateSettingsModal({
  open,
  onOpenChange,
  candidateId,
  candidateToken,
  candidateName = "",
  candidateEmail = "",
  onDeleteSuccess,
}) {
  const [view, setView] = React.useState("home");
  const [deleteConfirmOpen, setDeleteConfirmOpen] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [sending, setSending] = React.useState(false);
  const [subject, setSubject] = React.useState("");
  const [message, setMessage] = React.useState("");
  const [formError, setFormError] = React.useState("");
  const [sendSuccess, setSendSuccess] = React.useState("");

  React.useEffect(() => {
    if (!open) {
      setView("home");
      setDeleteConfirmOpen(false);
      setDeleting(false);
      setSending(false);
      setSubject("");
      setMessage("");
      setFormError("");
      setSendSuccess("");
    }
  }, [open]);

  const goHelp = () => setView("faq");
  const goContact = () => {
    setSubject("");
    setMessage("");
    setFormError("");
    setSendSuccess("");
    setView("contact");
  };

  const handleDelete = async (event) => {
    event.preventDefault();
    if (!candidateId || !candidateToken || deleting) return;
    setDeleting(true);
    try {
      await axios.delete(`${API}/candidate/${candidateId}/account`, {
        headers: AuthHeader(candidateToken),
      });
      toast.success("Your account was deleted.");
      setDeleteConfirmOpen(false);
      onOpenChange?.(false);
      onDeleteSuccess?.();
    } catch (error) {
      const detail = error?.response?.data?.detail || "We couldn't delete your account right now.";
      toast.error(detail);
      setDeleting(false);
    }
  };

  const handleSend = async (event) => {
    event.preventDefault();
    setFormError("");
    setSendSuccess("");
    if (!candidateId || !candidateToken) {
      setFormError("Your session expired. Please sign in again.");
      return;
    }
    const cleanedSubject = subject.trim();
    const cleanedMessage = message.trim();
    if (cleanedSubject.length < 3) {
      setFormError("Please add a subject for your message.");
      return;
    }
    if (cleanedMessage.length < 5) {
      setFormError("Please add a longer message so we can help.");
      return;
    }

    setSending(true);
    try {
      await axios.post(
        `${API}/candidate/${candidateId}/help`,
        {
          candidate_id: candidateId,
          subject: cleanedSubject,
          message: cleanedMessage,
        },
        { headers: AuthHeader(candidateToken) }
      );
      setSendSuccess("Thanks, your message was sent.");
      toast.success("Message sent to support.");
      setSubject("");
      setMessage("");
    } catch (error) {
      const detail = error?.response?.data?.detail || "We couldn't send your message right now.";
      setFormError(detail);
      toast.error(detail);
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-hidden p-0 sm:rounded-2xl">
          <div className="bg-[#FBFBF9]">
            <DialogHeader className="border-b border-black/[0.06] px-6 py-5 text-left">
              <DialogTitle className="text-[18px] font-medium tracking-tight text-[#1F1F1F]">
                Settings
              </DialogTitle>
              <DialogDescription className="text-[13px] text-[#6B6B69]">
                Manage your account and get help from Pontis.
              </DialogDescription>
            </DialogHeader>

            <div className="max-h-[calc(85vh-84px)] overflow-y-auto eve-scroll px-6 py-6">
              {view === "home" ? (
                <div className="grid gap-4">
                  <button
                    type="button"
                    onClick={goHelp}
                    data-testid="settings-need-help-btn"
                    className="flex items-center justify-between rounded-2xl border border-black/[0.06] bg-white px-4 py-4 text-left hover:border-black/[0.12] hover:bg-black/[0.01] transition-colors"
                  >
                    <div className="flex items-center gap-3">
                      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-[#E7E3F0]">
                        <HelpCircle className="h-5 w-5 text-[#7B6FB8]" strokeWidth={1.8} />
                      </span>
                      <div>
                        <p className="text-[14px] font-medium text-[#1F1F1F]">Need Help</p>
                        <p className="text-[12px] text-[#6B6B69]">Browse FAQs or send a message to the team.</p>
                      </div>
                    </div>
                    <span className="text-[12px] text-[#9A9A98]">Open</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => setDeleteConfirmOpen(true)}
                    data-testid="settings-delete-account-btn"
                    className="flex items-center justify-between rounded-2xl border border-red-200 bg-red-50 px-4 py-4 text-left hover:bg-red-100/70 transition-colors"
                  >
                    <div className="flex items-center gap-3">
                      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-red-100">
                        <Trash2 className="h-5 w-5 text-red-600" strokeWidth={1.8} />
                      </span>
                      <div>
                        <p className="text-[14px] font-medium text-red-700">Delete Account</p>
                        <p className="text-[12px] text-red-600/80">Permanently remove your profile and data.</p>
                      </div>
                    </div>
                    <span className="text-[12px] text-red-500">Open</span>
                  </button>
                </div>
              ) : view === "faq" ? (
                <ViewShell
                  title="Need Help"
                  description="Here are a few quick answers candidates ask most often."
                  onBack={() => setView("home")}
                >
                  <div className="space-y-3">
                    {FAQ_ITEMS.map((item) => (
                      <div key={item.q} className="rounded-2xl border border-black/[0.06] bg-white px-4 py-4">
                        <p className="text-[14px] font-medium text-[#1F1F1F]">{item.q}</p>
                        <p className="mt-1.5 text-[13px] leading-relaxed text-[#4A4A48]">{item.a}</p>
                      </div>
                    ))}
                  </div>

                  <div className="mt-5 rounded-2xl border border-black/[0.06] bg-[#F7F7F4] px-4 py-4">
                    <div className="flex items-start gap-3">
                      <span className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-full bg-white">
                        <Mail className="h-4.5 w-4.5 text-[#7A7A78]" strokeWidth={1.8} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="text-[14px] font-medium text-[#1F1F1F]">Other</p>
                        <p className="mt-1 text-[13px] leading-relaxed text-[#4A4A48]">
                          If your question is not covered here, send us a note and we will help from there.
                        </p>
                        <button
                          type="button"
                          data-testid="settings-other-btn"
                          onClick={goContact}
                          className="mt-3 rounded-full bg-[#1F1F1F] px-4 py-2 text-[13px] font-medium text-white hover:bg-black transition-colors"
                        >
                          Contact support
                        </button>
                      </div>
                    </div>
                  </div>
                </ViewShell>
              ) : (
                <ViewShell
                  title="Contact Support"
                  description={`Send your question to ${SUPPORT_EMAIL_TO}. We will reply as soon as we can.`}
                  onBack={() => setView("faq")}
                >
                  <form onSubmit={handleSend} className="space-y-4">
                    <div className="rounded-2xl border border-black/[0.06] bg-white px-4 py-3">
                      <p className="text-[11px] uppercase tracking-wide text-[#9A9A98]">From</p>
                      <p className="mt-1 text-[14px] font-medium text-[#1F1F1F]">
                        {candidateName || "Candidate"}
                      </p>
                      <p className="text-[12px] text-[#6B6B69]">{candidateEmail || "No email on file"}</p>
                    </div>

                    <div className="rounded-2xl border border-black/[0.06] bg-white px-4 py-3">
                      <p className="text-[11px] uppercase tracking-wide text-[#9A9A98]">To</p>
                      <p className="mt-1 text-[14px] font-medium text-[#1F1F1F]">{SUPPORT_EMAIL_TO}</p>
                    </div>

                    <label className="block">
                      <span className="mb-1.5 block text-[12px] font-medium text-[#4A4A48]">Subject</span>
                      <input
                        value={subject}
                        onChange={(e) => setSubject(e.target.value)}
                        placeholder="How can we help?"
                        className="w-full rounded-2xl border border-black/[0.08] bg-white px-4 py-3 text-[14px] text-[#1F1F1F] outline-none transition-colors focus:border-black/[0.2]"
                      />
                    </label>

                    <label className="block">
                      <span className="mb-1.5 block text-[12px] font-medium text-[#4A4A48]">Message</span>
                      <textarea
                        value={message}
                        onChange={(e) => setMessage(e.target.value)}
                        rows={7}
                        placeholder="Tell us what you need help with."
                        className="w-full rounded-2xl border border-black/[0.08] bg-white px-4 py-3 text-[14px] text-[#1F1F1F] outline-none transition-colors focus:border-black/[0.2] resize-none"
                      />
                    </label>

                    {formError ? (
                      <p data-testid="help-error" className="text-[12px] text-red-600">{formError}</p>
                    ) : null}
                    {sendSuccess ? (
                      <p data-testid="help-success" className="text-[12px] text-[#2E7538]">{sendSuccess}</p>
                    ) : null}

                    <div className="flex items-center justify-between gap-3">
                      <button
                        type="button"
                        onClick={() => setView("faq")}
                        className="rounded-full bg-black/[0.05] px-4 py-2.5 text-[13px] font-medium text-[#1F1F1F] hover:bg-black/[0.08] transition-colors"
                      >
                        Back to FAQ
                      </button>
                      <button
                        type="submit"
                        disabled={sending}
                        data-testid="help-send-btn"
                        className="rounded-full bg-[#1F1F1F] px-5 py-2.5 text-[13px] font-medium text-white hover:bg-black transition-colors disabled:cursor-not-allowed disabled:bg-black/[0.14]"
                      >
                        {sending ? "Sending..." : "Send"}
                      </button>
                    </div>
                  </form>
                </ViewShell>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleteConfirmOpen} onOpenChange={setDeleteConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete account?</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete your account?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              data-testid="confirm-delete-account-btn"
              onClick={handleDelete}
              disabled={deleting}
              className="bg-red-600 text-white hover:bg-red-700"
            >
              {deleting ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
