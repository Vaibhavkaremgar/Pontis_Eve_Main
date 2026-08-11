import React from "react";
import axios from "axios";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { Toaster, toast } from "sonner";
import { Bell } from "lucide-react";

import Sidebar from "../components/Sidebar";
import ChatHub from "../components/ChatHub";
import LivingProfile from "../components/LivingProfile";
import {
  MOCK_RECENT_ACTIVITY,
  QUICK_ACTIONS,
} from "../mock";
import { loadOnboardingState, saveOnboardingState, clearOnboardingState } from "../lib/onboardingStorage";
import { useNavigate } from "react-router-dom";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

function computeStrength(p) {
  const fields = [p.name, p.email, p.phone, p.headline, p.location, p.bio];
  const filled = fields.filter((f) => typeof f === "string" && f.trim() !== "").length;
  const hasExp = Array.isArray(p.experience) && p.experience.length > 0 ? 1 : 0;
  const hasEdu = Array.isArray(p.education) && p.education.length > 0 ? 1 : 0;
  const hasSkills = Array.isArray(p.keySkills) && p.keySkills.length > 0 ? 1 : 0;
  const total = filled + hasExp + hasEdu + hasSkills;
  const percent = Math.round((total / 9) * 100);
  const label = percent >= 70 ? "Strong" : percent >= 40 ? "Developing" : "Building";
  return { strengthPercent: percent, strength: label };
}

function buildFallbackProfile(isOpenToMatches = true) {
  return {
    avatar: null,
    isOpenToMatches,
    strength: "Building",
    strengthPercent: 0,
    name: "",
    email: "",
    phone: "",
    headline: "",
    location: "",
    bio: "",
    experience: [],
    education: [],
    keySkills: [],
    experience_years: null,
    availability: "",
    preferred_roles: [],
    certifications: [],
    additional_information: "",
  };
}

function applyStrength(profile) {
  return { ...profile, ...computeStrength(profile) };
}

function ResizeHandle({ testId, subtle = false }) {
  return (
    <PanelResizeHandle
      data-testid={testId}
      className="group relative w-[6px] shrink-0 bg-transparent"
    >
      <div
        className={`absolute inset-y-0 left-1/2 -translate-x-1/2 w-px transition-colors ${
          subtle
            ? "bg-transparent group-hover:bg-black/[0.06] group-data-[resize-handle-active]:bg-black/[0.14]"
            : "bg-black/[0.05] group-hover:bg-black/[0.15] group-data-[resize-handle-active]:bg-black/[0.28]"
        }`}
      />
    </PanelResizeHandle>
  );
}

function Dashboard() {
  const navigate = useNavigate();
  const stored = React.useMemo(() => loadOnboardingState(), []);
  const [candidateId, setCandidateId] = React.useState(() => loadOnboardingState().candidateId ?? null);
  const isOpenToMatches = stored.isOpenToMatches ?? true;

  const [activeTab, setActiveTab] = React.useState(() => {
    if (stored.newlyOnboarded) {
      saveOnboardingState({ ...stored, newlyOnboarded: false });
      return "profile";
    }
    return "new-jobs";
  });

  const [userProfile, setUserProfile] = React.useState(() =>
    applyStrength(buildFallbackProfile(isOpenToMatches))
  );

  // Load real profile from PostgreSQL on mount
  React.useEffect(() => {
    if (!candidateId) return;
    console.log("Current candidateId:", candidateId);
    axios
      .get(`${API}/candidate/${candidateId}/profile`)
      .then((res) => {
        const data = res.data;
        setUserProfile(applyStrength({
          avatar: null,
          isOpenToMatches,
          name: data.name ?? "",
          email: data.email ?? "",
          phone: data.phone ?? "",
          headline: data.headline ?? "",
          location: data.location ?? "",
          bio: data.bio ?? "",
          experience: data.experience ?? [],
          education: data.education ?? [],
          keySkills: data.keySkills ?? [],
          experience_years: data.experience_years ?? null,
          availability: data.availability ?? "",
          preferred_roles: data.preferred_roles ?? [],
          certifications: data.certifications ?? [],
          additional_information: data.additional_information ?? "",
        }));
      })
      .catch(() => {
        // Fall back to onboarding parsed profile if API fails
        const parsed = stored.parsedProfile ?? {};
        setUserProfile(applyStrength({
          avatar: null,
          isOpenToMatches,
          name: parsed.name ?? "",
          email: parsed.email ?? "",
          phone: parsed.phone ?? "",
          headline: parsed.headline ?? "",
          location: parsed.location ?? "",
          bio: parsed.bio ?? "",
          experience: parsed.experience ?? [],
          education: parsed.education ?? [],
          keySkills: parsed.keySkills ?? [],
          experience_years: parsed.experience_years ?? null,
          availability: parsed.availability ?? "",
          preferred_roles: parsed.preferred_roles ?? [],
          certifications: parsed.certifications ?? [],
          additional_information: parsed.additional_information ?? "",
        }));
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateId]);

  const refreshProfile = React.useCallback(async () => {
    if (!candidateId) return;
    try {
      const res = await axios.get(`${API}/candidate/${candidateId}/profile`);
      const data = res.data;
      setUserProfile((prev) =>
        applyStrength({
          ...prev,
          name: data.name ?? prev.name,
          email: data.email ?? prev.email,
          phone: data.phone ?? prev.phone,
          headline: data.headline ?? prev.headline,
          location: data.location ?? prev.location,
          bio: data.bio ?? prev.bio,
          experience: data.experience?.length ? data.experience : prev.experience,
          education: data.education?.length ? data.education : prev.education,
          keySkills: data.keySkills?.length ? data.keySkills : prev.keySkills,
          experience_years: data.experience_years != null ? data.experience_years : prev.experience_years,
          availability: data.availability ?? prev.availability,
          preferred_roles: data.preferred_roles?.length ? data.preferred_roles : prev.preferred_roles,
          certifications: data.certifications?.length ? data.certifications : prev.certifications,
          additional_information: data.additional_information ?? prev.additional_information,
        })
      );
    } catch (err) {
      console.error("refreshProfile failed", err);
    }
  }, [candidateId]);

  const handleLogout = React.useCallback(() => {
    clearOnboardingState();
    navigate("/");
  }, [navigate]);

  const handleToggleOpenToMatches = React.useCallback(() => {
    setUserProfile((prev) => {
      const next = { ...prev, isOpenToMatches: !prev.isOpenToMatches };
      const s = loadOnboardingState();
      saveOnboardingState({ ...s, isOpenToMatches: next.isOpenToMatches });
      return next;
    });
  }, []);

  const [chats, setChats] = React.useState([
    { id: "msg-1", sender: "eve", content: "Hi there — great chatting with you. I've got your resume in front of me. Let's fill in a few details that are missing from your profile." },
  ]);

  // Opportunities: count pending (no candidate_response) recruiter-interest notifications
  const [opportunitiesCount, setOpportunitiesCount] = React.useState(0);
  React.useEffect(() => {
    if (!candidateId) return;
    const fetchCount = () =>
      axios
        .get(`${API}/candidate/${candidateId}/opportunities`)
        .then((res) => {
          const pending = (res.data || []).filter((o) => !o.candidate_response).length;
          setOpportunitiesCount(pending);
        })
        .catch(() => {});
    fetchCount();
    const interval = setInterval(fetchCount, 30000);
    return () => clearInterval(interval);
  }, [candidateId]);

  // Update greeting once real profile loads
  React.useEffect(() => {
    const firstName = userProfile.name?.split(" ")[0];
    if (!firstName) return;
    setChats((prev) =>
      prev[0]?.id === "msg-1"
        ? [{ ...prev[0], content: `Hi ${firstName} — great chatting with you. I've got your resume in front of me. Let's fill in a few details that are missing from your profile.` }, ...prev.slice(1)]
        : prev
    );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userProfile.name]);
  const [inputValue, setInputValue] = React.useState("");
  const [availableJobs, setAvailableJobs] = React.useState([]);
  const [documents, setDocuments] = React.useState({ resume: null, certificates: [] });
  const [docsLoading, setDocsLoading] = React.useState(false);
  const [selectedJob, setSelectedJob] = React.useState(null);
  const [sending, setSending] = React.useState(false);

  // Load real job recommendations from backend
  const fetchJobs = React.useCallback(() => {
    if (!candidateId) return;
    axios
      .get(`${API}/candidate/${candidateId}/jobs`)
      .then((res) => {
        setAvailableJobs(res.data || []);
        setSelectedJob((prev) => prev ?? (res.data?.[0] || null));
      })
      .catch(() => {});
  }, [candidateId]);

  React.useEffect(() => {
    fetchJobs();
    const interval = setInterval(fetchJobs, 60000);
    return () => clearInterval(interval);
  }, [fetchJobs]);

  React.useEffect(() => {
    if (!candidateId) return;
    setDocsLoading(true);
    axios
      .get(`${API}/candidate/${candidateId}/documents`)
      .then((res) => setDocuments(res.data))
      .catch(() => {})
      .finally(() => setDocsLoading(false));
  }, [candidateId]);

  const sessionIdRef = React.useRef(
    `sess-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  );

  const handleSendMessage = async (e) => {
    e.preventDefault();
    const text = inputValue.trim();
    if (!text || sending) return;

    const userMsg = { id: `u-${Date.now()}`, sender: "user", content: text };
    const nextChats = [...chats, userMsg];
    setChats(nextChats);
    setInputValue("");
    setSending(true);

    try {
      const historyPayload = nextChats
        .filter((c) => c.sender === "user" || c.sender === "eve")
        .map((c) => ({
          role: c.sender === "user" ? "user" : "assistant",
          content: c.content,
        }));

      const res = await axios.post(`${API}/chat`, {
        messages: historyPayload,
        session_id: sessionIdRef.current,
        candidate_id: candidateId,
      });

      const reply = res?.data?.reply?.trim();
      if (reply) {
        setChats((prev) => [
          ...prev,
          { id: `e-${Date.now()}`, sender: "eve", content: reply },
        ]);
      } else {
        toast.error("Eve didn't respond. Try again?");
      }

      // If LLM returned profile updates, refresh the profile panel
      if (res?.data?.profile_updates && candidateId) {
        await refreshProfile();
      }
    } catch (err) {
      console.error("chat error", err);
      toast.error("Couldn't reach Eve right now.");
    } finally {
      setSending(false);
    }
  };

  const handleResumeReplaced = React.useCallback((filename, newProfile) => {
    setDocuments((prev) => ({ ...prev, resume: { filename } }));
    // Always refresh from DB after resume replace to get the preserved voice fields
    refreshProfile();
  }, [refreshProfile]);

  const handleCertUploaded = React.useCallback((cert) => {
    setDocuments((prev) => ({ ...prev, certificates: [...prev.certificates, cert] }));
  }, []);

  const handleCertReplaced = React.useCallback((certId, filename) => {
    setDocuments((prev) => ({
      ...prev,
      certificates: prev.certificates.map((c) =>
        c.id === certId ? { ...c, filename } : c
      ),
    }));
  }, []);

  const handleTrackJob = async (jobId) => {
    const job = availableJobs.find((j) => j.id === jobId);
    if (!job || !candidateId) return;
    const willTrack = !job.tracked;
    // Optimistic update
    setAvailableJobs((prev) =>
      prev.map((j) => (j.id === jobId ? { ...j, tracked: willTrack } : j))
    );
    try {
      if (willTrack) {
        await axios.post(`${API}/candidate/${candidateId}/jobs/${jobId}/track`);
        toast.success(`Tracking ${job.company}`);
      } else {
        await axios.delete(`${API}/candidate/${candidateId}/jobs/${jobId}/track`);
        toast("Untracked");
      }
    } catch {
      // Revert on failure
      setAvailableJobs((prev) =>
        prev.map((j) => (j.id === jobId ? { ...j, tracked: !willTrack } : j))
      );
      toast.error("Could not update tracking.");
    }
  };

  const handleDismissJob = async (jobId) => {
    const job = availableJobs.find((j) => j.id === jobId);
    if (!job || !candidateId) return;
    setAvailableJobs((prev) => prev.filter((j) => j.id !== jobId));
    if (selectedJob?.id === jobId) {
      setSelectedJob(availableJobs.find((j) => j.id !== jobId) || null);
    }
    toast("Removed from feed", { description: "You won't see this again." });
    try {
      await axios.post(`${API}/candidate/${candidateId}/jobs/${jobId}/dismiss`);
    } catch {
      // Silently re-fetch to restore state if dismiss failed
      fetchJobs();
    }
  };

  return (
    <div
      className="h-screen w-full bg-[#FBFBF9] text-[#1F1F1F] overflow-hidden flex flex-col"
      data-testid="app-shell"
    >
      <Toaster position="top-right" richColors closeButton />

      {/* Dashboard top header with Bell */}
      <div className="shrink-0 flex items-center justify-end px-5 py-2 border-b border-black/[0.05]">
        <button
          data-testid="header-bell-btn"
          onClick={() => setActiveTab("opportunities")}
          className="relative p-1.5 rounded-lg text-[#4A4A48] hover:bg-black/[0.04] transition-colors"
          aria-label="Notifications"
        >
          <Bell className="w-[18px] h-[18px]" strokeWidth={1.5} />
          {opportunitiesCount > 0 && (
            <span
              data-testid="header-bell-badge"
              className="absolute -top-0.5 -right-0.5 min-w-[16px] h-[16px] px-1 rounded-full bg-[#1F1F1F] text-white text-[9px] font-medium flex items-center justify-center"
            >
              {opportunitiesCount}
            </span>
          )}
        </button>
      </div>

      <PanelGroup direction="horizontal" className="flex-1 min-h-0">
        <Panel id="left-panel" order={1} defaultSize={18} minSize={12} maxSize={28} className="h-full">
          <Sidebar
            activeTab={activeTab}
            setActiveTab={setActiveTab}
            userProfile={userProfile}
            jobsCount={availableJobs.length}
            opportunitiesCount={opportunitiesCount}
            recentActivity={MOCK_RECENT_ACTIVITY}
            onLogout={handleLogout}
          />
        </Panel>

        <ResizeHandle testId="resize-handle-left" />

        <Panel id="center-panel" order={2} defaultSize={32} minSize={22} className="h-full">
          <ChatHub
            chats={chats}
            inputValue={inputValue}
            setInputValue={setInputValue}
            onSend={handleSendMessage}
            sending={sending}
            quickActions={QUICK_ACTIONS}
          />
        </Panel>

        <ResizeHandle testId="resize-handle-right" subtle />

        <Panel id="right-panel" order={3} defaultSize={50} minSize={30} className="h-full">
          <LivingProfile
            activeTab={activeTab}
            userProfile={userProfile}
            jobs={availableJobs}
            documents={documents}
            docsLoading={docsLoading}
            candidateId={candidateId}
            selectedJob={selectedJob}
            setSelectedJob={setSelectedJob}
            onTrackJob={handleTrackJob}
            onDismissJob={handleDismissJob}
            onToggleOpenToMatches={handleToggleOpenToMatches}
            onResumeReplaced={handleResumeReplaced}
            onCertUploaded={handleCertUploaded}
            onCertReplaced={handleCertReplaced}
            onInterested={fetchJobs}
          />
        </Panel>
      </PanelGroup>
    </div>
  );
}

export default function DashboardGuard() {
  const navigate = useNavigate();
  const candidateId = loadOnboardingState().candidateId ?? null;

  React.useEffect(() => {
    if (!candidateId) navigate("/", { replace: true });
  }, [candidateId, navigate]);

  if (!candidateId) return null;
  return <Dashboard />;
}
