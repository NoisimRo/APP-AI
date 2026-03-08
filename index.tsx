import React, { useState, useRef, useEffect, useMemo } from "react";
import { createRoot } from "react-dom/client";
import { GoogleGenAI } from "@google/genai";
import {
  Scale,
  AlertTriangle,
  MessageSquare,
  FileText,
  Search,
  Upload,
  CheckCircle,
  XCircle,
  Gavel,
  BookOpen,
  Send,
  Loader2,
  Trash2,
  Database,
  LayoutDashboard,
  Filter,
  CheckSquare,
  Square,
  PieChart,
  Settings,
  Menu,
  Cloud,
  Wifi,
  RefreshCw,
  FolderInput,
  X,
  Eye,
  ChevronDown,
  ChevronUp,
  GraduationCap,
  Download,
  Pencil
} from "lucide-react";

// --- Types ---

type AppMode = 'dashboard' | 'datalake' | 'drafter' | 'redflags' | 'chat' | 'clarification' | 'rag' | 'training';

interface UploadedFile {
  id: string;
  name: string;
  type: string;
  content: string; // Base64
  isActive: boolean; // Controls if file is sent to Context
  metadata?: {
    year?: string;
    bulletin?: string;
    critics?: string[];
    cpv?: string;
    ruling?: 'Admis' | 'Respins' | 'Unknown';
    sourcePath?: string;
  };
}

interface Message {
  role: 'user' | 'model';
  text: string;
}

// --- Helper Functions ---

const generateId = () => Math.random().toString(36).substr(2, 9);

const fetchStream = async (
  url: string,
  body: any,
  onChunk: (text: string) => void,
  onDone: (meta: any) => void,
  onError: (error: string) => void,
) => {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const errBody = await response.json();
      const d = errBody.detail;
      if (typeof d === 'string') detail = d;
      else if (Array.isArray(d)) detail = d.map((e: any) => e.msg || JSON.stringify(e)).join('; ');
      else if (d) detail = JSON.stringify(d);
      else detail = JSON.stringify(errBody);
    } catch { /* ignore parse errors */ }
    onError(detail);
    return;
  }
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop()!;
    for (const part of parts) {
      if (part.startsWith('data: ')) {
        try {
          const data = JSON.parse(part.slice(6));
          if (data.error) { onError(typeof data.error === 'string' ? data.error : JSON.stringify(data.error)); return; }
          if (data.text) onChunk(data.text);
          if (data.done) onDone(data);
        } catch { /* ignore malformed SSE */ }
      }
    }
  }
};

const parseFilenameMetadata = (filename: string) => {
  // Expected: BO2025 - [Bulletin] - [Critics] - [CPV] - [A/R].txt/pdf
  const metadata: UploadedFile['metadata'] = { ruling: 'Unknown' };

  if (filename.includes('BO2025') || filename.includes('BO2024')) {
    metadata.year = filename.substring(2, 6);
  }

  const nameWithoutExt = filename.split('.')[0];
  if (nameWithoutExt.endsWith('A') || nameWithoutExt.endsWith(' A')) metadata.ruling = 'Admis';
  if (nameWithoutExt.endsWith('R') || nameWithoutExt.endsWith(' R')) metadata.ruling = 'Respins';

  return metadata;
};

const fileToBase64 = (file: File): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = () => {
      const result = reader.result as string;
      const base64 = result.split(',')[1];
      resolve(base64);
    };
    reader.onerror = error => reject(error);
  });
};

// --- Markdown Formatter ---

// Map heading keywords to emoji
const headingEmoji = (title: string): string => {
  const t = title.toLowerCase();
  if (t.includes('enun')) return '📋';
  if (t.includes('cerin')) return '✅';
  if (t.includes('rezolv')) return '⚖️';
  if (t.includes('note') && t.includes('trainer')) return '🎓';
  if (t.includes('scenariu')) return '🎭';
  if (t.includes('context')) return 'ℹ️';
  if (t.includes('argumen') || t.includes('pro') || t.includes('contra')) return '💬';
  if (t.includes('concluzi') || t.includes('concluz')) return '🏁';
  if (t.includes('răspuns') || t.includes('soluți') || t.includes('varianta corect')) return '✔️';
  if (t.includes('întrebare') || t.includes('quiz')) return '❓';
  if (t.includes('rol')) return '👥';
  if (t.includes('erori') || t.includes('greșel')) return '⚠️';
  if (t.includes('cronolog') || t.includes('pași') || t.includes('etap')) return '📅';
  if (t.includes('compar')) return '🔀';
  if (t.includes('legisl') || t.includes('legal') || t.includes('temei')) return '⚖️';
  if (t.includes('jurispruden')) return '🏛️';
  return '';
};

const formatMarkdown = (text: string): string => {
  return text
    // Escape HTML
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    // Citation links: [[BO2025_1000]] -> clickable pill tag
    .replace(/\[\[(BO\d{4}_\d+)\]\]/g, '<a href="#" data-decision="$1" onclick="window.__openDecision && window.__openDecision(\'$1\'); return false;" style="display:inline-flex;align-items:center;background:#eff6ff;color:#1d4ed8;padding:2px 10px;border-radius:9999px;border:1px solid #bfdbfe;font-family:monospace;font-size:0.8em;font-weight:600;cursor:pointer;text-decoration:none;margin:2px 3px;transition:background 0.15s" onmouseover="this.style.background=\'#dbeafe\'" onmouseout="this.style.background=\'#eff6ff\'">$1</a>')
    // Bold: **text**
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    // Italic: *text*
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // Headers with emoji
    .replace(/^### (.+)$/gm, (_m, title) => {
      const em = headingEmoji(title);
      return `<h3 style="font-size:1rem;font-weight:700;margin:1rem 0 0.4rem 0;color:#1e293b">${em ? em + ' ' : ''}${title}</h3>`;
    })
    .replace(/^## (.+)$/gm, (_m, title) => {
      const em = headingEmoji(title);
      return `<h2 style="font-size:1.1rem;font-weight:700;margin:1.2rem 0 0.4rem 0;color:#1e293b">${em ? em + ' ' : ''}${title}</h2>`;
    })
    .replace(/^# (.+)$/gm, (_m, title) => {
      const em = headingEmoji(title);
      return `<h1 style="font-size:1.25rem;font-weight:700;margin:1.2rem 0 0.4rem 0;color:#1e293b">${em ? em + ' ' : ''}${title}</h1>`;
    })
    // Numbered lists: 1. item — compact spacing
    .replace(/^(\d+)\.\s+(.+)$/gm, '<div style="display:flex;gap:0.5rem;margin:0.1rem 0 0.1rem 1rem;line-height:1.5"><span style="color:#64748b;min-width:1.2rem;font-weight:600">$1.</span><span>$2</span></div>')
    // Bullet lists: - item — compact spacing with FA icon
    .replace(/^[-•]\s+(.+)$/gm, '<div style="display:flex;gap:0.5rem;margin:0.1rem 0 0.1rem 1rem;line-height:1.5"><span style="color:#d97706;font-size:0.6rem;margin-top:0.5rem">◆</span><span>$1</span></div>')
    // Horizontal rules
    .replace(/^---$/gm, '<hr style="border:none;border-top:1px solid #e2e8f0;margin:1rem 0"/>')
    // Paragraphs: double newlines
    .replace(/\n\n/g, '</p><p style="margin:0.75rem 0">')
    // Single newlines within paragraphs
    .replace(/\n/g, '<br/>')
    // Wrap in paragraph
    .replace(/^/, '<p style="margin:0 0 0.5rem 0">')
    .replace(/$/, '</p>');
};

// Character counter for text inputs
const CharCounter = ({ value, maxLength }: { value: string; maxLength: number }) => {
  const len = value.length;
  const words = value.trim() ? value.trim().split(/\s+/).length : 0;
  const pct = len / maxLength;
  const color = pct > 1 ? 'text-red-600 font-bold' : pct > 0.9 ? 'text-amber-600' : 'text-slate-400';
  return (
    <div className={`text-xs mt-1 flex justify-end gap-2 ${color}`}>
      <span>{words} cuv.</span>
      <span>{len.toLocaleString()} / {maxLength.toLocaleString()} car.{pct > 1 ? ' — limită depășită!' : ''}</span>
    </div>
  );
};

// Critique codes legend (CNSC standard)
const CRITIQUE_LEGEND: Record<string, string> = {
  D1: "Cerințe restrictive — experiență similară, calificare, specificații tehnice",
  D2: "Criterii de atribuire / factori de evaluare netransparenți sau subiectivi",
  D3: 'Denumiri de produse/mărci fără sintagma \u201Esau echivalent\u201D',
  D4: "Lipsa răspuns clar la solicitările de clarificări",
  D5: "Forma de constituire a garanției de participare",
  D6: "Clauze contractuale inechitabile sau excesive",
  D7: "Nedivizarea achiziției pe loturi",
  D8: "Alte critici documentație",
  DAL: "Alte critici documentație",
  R1: "Contestații proces-verbal ședință deschidere oferte",
  R2: "Respingerea ofertei ca neconformă sau inacceptabilă",
  R3: "Prețul neobișnuit de scăzut al altor ofertanți",
  R4: "Documente calificare alți ofertanți / mod de evaluare",
  R5: "Lipsa precizării motivelor de respingere",
  R6: "Lipsa solicitare clarificări / apreciere incorectă răspunsuri",
  R7: "Anularea fără temei legal a procedurii",
  R8: "Alte critici rezultat",
  RAL: "Alte critici rezultat",
};

// --- Components ---

const SidebarItem = ({ 
  icon: Icon, 
  label, 
  active, 
  onClick,
  badge
}: { 
  icon: any, 
  label: string, 
  active: boolean, 
  onClick: () => void,
  badge?: number
}) => (
  <button 
    onClick={onClick}
    className={`w-full flex items-center gap-3 px-4 py-3 text-sm font-medium transition-all rounded-md mb-1 ${
      active 
        ? "bg-blue-600 text-white shadow-md" 
        : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
    }`}
  >
    <Icon size={18} />
    <span className="flex-1 text-left">{label}</span>
    {badge !== undefined && badge > 0 && (
      <span className="bg-blue-500/20 text-blue-200 text-xs px-2 py-0.5 rounded-full">{badge}</span>
    )}
  </button>
);

const StatCard = ({ label, value, icon: Icon, color }: { label: string, value: string | number, icon: any, color: string }) => (
  <div className="bg-white p-6 rounded-xl border border-slate-100 shadow-sm flex items-center gap-4">
    <div className={`p-4 rounded-full ${color} bg-opacity-10`}>
      <Icon className={color.replace('bg-', 'text-')} size={24} />
    </div>
    <div>
      <p className="text-slate-500 text-sm font-medium">{label}</p>
      <p className="text-2xl font-bold text-slate-800">{value}</p>
    </div>
  </div>
);

// --- Main Application ---

const App = () => {
  const [mode, setMode] = useState<AppMode>('dashboard');
  const [apiKey] = useState(process.env.API_KEY || "");
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [fileSearch, setFileSearch] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [apiDecisions, setApiDecisions] = useState<any[]>([]);
  const [apiDecisionsTotal, setApiDecisionsTotal] = useState(0);
  const [apiDecisionsPage, setApiDecisionsPage] = useState(1);
  const [isLoadingDecisions, setIsLoadingDecisions] = useState(false);
  const [dbStats, setDbStats] = useState<any>(null);
  const [filterRuling, setFilterRuling] = useState("");
  const [filterType, setFilterType] = useState("");
  const [filterYear, setFilterYear] = useState("");

  // Chat/Interaction States
  const [chatMessages, setChatMessages] = useState<Message[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [generatedContent, setGeneratedContent] = useState<string>("");
  const [generatedDecisionRefs, setGeneratedDecisionRefs] = useState<string[]>([]);

  // Specialized Input States
  const [drafterContext, setDrafterContext] = useState({ facts: "", authorityArgs: "", legalGrounds: "" });
  const [clarificationClause, setClarificationClause] = useState("");
  const [memoTopic, setMemoTopic] = useState("");

  // Red Flags States
  const [redFlagsText, setRedFlagsText] = useState("");
  const [redFlagsResults, setRedFlagsResults] = useState<any[]>([]);
  const [redFlagsTab, setRedFlagsTab] = useState<'manual' | 'upload'>('manual');
  const [uploadedDocDrafter, setUploadedDocDrafter] = useState<{name: string, text: string} | null>(null);
  const [uploadedDocClarification, setUploadedDocClarification] = useState<{name: string, text: string} | null>(null);
  const [uploadedDocRag, setUploadedDocRag] = useState<{name: string, text: string} | null>(null);
  const [uploadedDocRedFlags, setUploadedDocRedFlags] = useState<{name: string, text: string} | null>(null);
  const [redFlagsProgress, setRedFlagsProgress] = useState("");

  // Training States
  const [trainingTema, setTrainingTema] = useState("");
  const [trainingTip, setTrainingTip] = useState("speta");
  const [trainingSelectedTypes, setTrainingSelectedTypes] = useState<string[]>(["speta", "quiz"]);
  const [trainingNivel, setTrainingNivel] = useState("mediu");
  const [trainingLungime, setTrainingLungime] = useState("mediu");
  const [trainingContext, setTrainingContext] = useState("");
  const [trainingResult, setTrainingResult] = useState<string>("");
  const [trainingLoading, setTrainingLoading] = useState(false);
  const [trainingActiveTab, setTrainingActiveTab] = useState<'material' | 'rezolvare' | 'note'>('material');
  const [trainingMeta, setTrainingMeta] = useState<any>(null);
  const [trainingShowContext, setTrainingShowContext] = useState(false);
  const [trainingEditing, setTrainingEditing] = useState(false);
  const [trainingEditedResult, setTrainingEditedResult] = useState<string | null>(null);
  const [trainingPublicTinta, setTrainingPublicTinta] = useState("");
  const [trainingMode, setTrainingMode] = useState<'individual' | 'batch' | 'program'>('individual');
  const [trainingBatchCount, setTrainingBatchCount] = useState(3);
  const [trainingBatchCustom, setTrainingBatchCustom] = useState(false);
  const [trainingProgramPlan, setTrainingProgramPlan] = useState("");
  const [uploadedDocTrainingContext, setUploadedDocTrainingContext] = useState<{name: string, text: string} | null>(null);
  const [uploadedDocTrainingPlan, setUploadedDocTrainingPlan] = useState<{name: string, text: string} | null>(null);
  const [trainingBatchProgress, setTrainingBatchProgress] = useState<{current: number, total: number, results: string[]} | null>(null);

  // Decision Viewer State
  const [viewingDecision, setViewingDecision] = useState<any | null>(null);
  const [isLoadingDecision, setIsLoadingDecision] = useState(false);
  const [decisionSearchTerm, setDecisionSearchTerm] = useState("");
  const [decisionSearchIndex, setDecisionSearchIndex] = useState(0);
  const decisionContentRef = useRef<HTMLDivElement>(null);

  const chatEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const ai = new GoogleGenAI({ apiKey });

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages, generatedContent]);

  // Register global handler for citation clicks
  useEffect(() => {
    (window as any).__openDecision = (decisionId: string) => {
      openDecision(decisionId);
    };
    return () => { delete (window as any).__openDecision; };
  }, []);

  // Scroll to active search match in decision modal
  useEffect(() => {
    if (!decisionContentRef.current || !decisionSearchTerm || decisionSearchTerm.length < 2) return;
    const timer = setTimeout(() => {
      const marks = decisionContentRef.current?.querySelectorAll('mark[data-match]');
      if (marks && marks.length > 0 && decisionSearchIndex < marks.length) {
        marks[decisionSearchIndex]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }, 50);
    return () => clearTimeout(timer);
  }, [decisionSearchIndex, decisionSearchTerm]);

  // Reset search index when search term changes
  useEffect(() => {
    setDecisionSearchIndex(0);
  }, [decisionSearchTerm]);

  useEffect(() => {
    setGeneratedContent("");
    setGeneratedDecisionRefs([]);
  }, [mode]);

  // Fetch global stats from API on mount
  useEffect(() => {
    const fetchStats = async () => {
      try {
        const response = await fetch('/api/v1/decisions/stats/overview');
        if (response.ok) {
          const data = await response.json();
          setDbStats(data);
        }
      } catch (error) {
        console.error('Failed to fetch stats:', error);
      }
    };
    fetchStats();
  }, []);

  // Fetch decisions for Data Lake (paginated + search)
  const fetchDecisions = async (page: number = 1, search?: string) => {
    setIsLoadingDecisions(true);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: '20' });
      if (search && search.trim()) {
        params.set('search', search.trim());
      }
      if (filterRuling) params.set('ruling', filterRuling);
      if (filterType) params.set('tip_contestatie', filterType);
      if (filterYear) params.set('year', filterYear);
      const response = await fetch(`/api/v1/decisions/?${params}`);
      if (response.ok) {
        const data = await response.json();
        setApiDecisions(data.decisions || []);
        setApiDecisionsTotal(data.total || 0);
        setApiDecisionsPage(data.page || 1);
      }
    } catch (error) {
      console.error('Failed to fetch decisions:', error);
    } finally {
      setIsLoadingDecisions(false);
    }
  };

  useEffect(() => {
    fetchDecisions(1);
  }, []);

  // Debounced search for Data Lake (triggers on search or filter change)
  useEffect(() => {
    const timer = setTimeout(() => {
      fetchDecisions(1, fileSearch);
    }, 300);
    return () => clearTimeout(timer);
  }, [fileSearch, filterRuling, filterType, filterYear]);

  // --- File Management ---

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      setIsUploading(true);
      const newFiles: UploadedFile[] = [];
      // Limit batch size to prevent freezing UI on 3000 files
      const MAX_BATCH = 100; 
      
      const fileList = Array.from(e.target.files);
      
      // Process in chunks if needed, but for now simple iteration
      for (let i = 0; i < fileList.length; i++) {
        const file = fileList[i];
        
        // Skip large files to prevent crash
        if (file.size > 5 * 1024 * 1024) continue;

        try {
          const base64 = await fileToBase64(file);
          newFiles.push({
            id: generateId(),
            name: file.name,
            type: file.type,
            content: base64,
            isActive: i < 10, // Only activate first 10 to save tokens
            metadata: {
              ...parseFilenameMetadata(file.name),
              sourcePath: (file as any).webkitRelativePath || "upload"
            }
          });
        } catch (err) {
          console.error("Error reading file", file.name, err);
        }
      }
      
      setFiles(prev => [...prev, ...newFiles]);
      setIsUploading(false);
    }
  };

  const removeFile = (id: string) => {
    setFiles(files.filter(f => f.id !== id));
  };

  const toggleFileActive = (id: string) => {
    setFiles(files.map(f => f.id === id ? { ...f, isActive: !f.isActive } : f));
  };

  const toggleAllActive = (active: boolean) => {
    const visibleFiles = files.filter(f => f.name.toLowerCase().includes(fileSearch.toLowerCase()));
    const visibleIds = new Set(visibleFiles.map(f => f.id));
    setFiles(files.map(f => visibleIds.has(f.id) ? { ...f, isActive: active } : f));
  };

  const activeFiles = useMemo(() => files.filter(f => f.isActive), [files]);
  const filteredFiles = useMemo(() => files.filter(f => f.name.toLowerCase().includes(fileSearch.toLowerCase())), [files, fileSearch]);

  const simulateSync = () => {
    setIsSyncing(true);
    setTimeout(() => {
      setIsSyncing(false);
      // Trigger file dialog
      if (fileInputRef.current) {
        fileInputRef.current.click();
      }
    }, 1500);
  }

  // --- Decision Viewer ---

  const openDecision = async (decisionId: string) => {
    setIsLoadingDecision(true);
    try {
      const response = await fetch(`/api/v1/decisions/${encodeURIComponent(decisionId)}`);
      if (response.ok) {
        const data = await response.json();
        setViewingDecision(data);
      } else {
        alert('Nu s-a putut încărca decizia.');
      }
    } catch (error) {
      console.error('Failed to fetch decision:', error);
      alert('Eroare la încărcarea deciziei.');
    } finally {
      setIsLoadingDecision(false);
    }
  };

  // --- API Interaction Handlers ---

  const getActiveContextParts = () => {
    return activeFiles.map(f => ({
      inlineData: { mimeType: f.type || 'text/plain', data: f.content }
    }));
  };

  const handleChat = async () => {
    if (!chatInput.trim()) return;
    const userMsg = chatInput;
    setChatMessages(prev => [...prev, { role: 'user', text: userMsg }]);
    setChatInput("");
    setIsLoading(true);

    // Add placeholder for streaming response
    setChatMessages(prev => [...prev, { role: 'model', text: '' }]);

    try {
      let accumulated = '';
      await fetchStream(
        '/api/v1/chat/stream',
        {
          message: userMsg,
          history: chatMessages.map(m => ({
            role: m.role === 'model' ? 'assistant' : m.role,
            content: m.text
          }))
        },
        (chunk) => {
          accumulated += chunk;
          setChatMessages(prev => {
            const updated = [...prev];
            updated[updated.length - 1] = { role: 'model', text: accumulated };
            return updated;
          });
        },
        (meta) => {
          // Append citations on completion
          if (meta.citations && meta.citations.length > 0) {
            const sources = "\n\n📚 **Surse:** " + meta.citations.map((c: any) => `[[${c.decision_id}]]`).join(" ");
            accumulated += sources;
            setChatMessages(prev => {
              const updated = [...prev];
              updated[updated.length - 1] = { role: 'model', text: accumulated };
              return updated;
            });
          }
        },
        (error) => {
          setChatMessages(prev => {
            const updated = [...prev];
            updated[updated.length - 1] = { role: 'model', text: `Eroare: ${error}` };
            return updated;
          });
        },
      );
    } catch (err) {
      console.error(err);
      setChatMessages(prev => {
        const updated = [...prev];
        updated[updated.length - 1] = {
          role: 'model',
          text: "Eroare la procesarea cererii. Asigură-te că backend-ul este pornit și conectat la baza de date."
        };
        return updated;
      });
    } finally {
      setIsLoading(false);
    }
  };

  const handleDrafting = async () => {
    setIsLoading(true);
    setGeneratedContent("");
    setGeneratedDecisionRefs([]);

    try {
      await fetchStream(
        '/api/v1/drafter/stream',
        {
          facts: drafterContext.facts,
          authority_args: drafterContext.authorityArgs,
          legal_grounds: drafterContext.legalGrounds,
        },
        (chunk) => {
          setGeneratedContent(prev => prev + chunk);
        },
        (meta) => {
          if (meta.decision_refs) {
            setGeneratedDecisionRefs(meta.decision_refs);
          }
        },
        (error) => {
          setGeneratedContent(`Eroare: ${error}`);
        },
      );
    } catch (err) {
      console.error(err);
      setGeneratedContent("Eroare la generare. Verifică că backend-ul este pornit.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleDocumentUpload = async (
    event: React.ChangeEvent<HTMLInputElement>,
    onTextExtracted?: (text: string) => void,
    setUploadedDoc?: (doc: {name: string, text: string}) => void,
  ) => {
    const file = event.target.files?.[0];
    if (!file) return;

    // Check file type
    const allowedTypes = ['.txt', '.md', '.pdf', '.doc', '.docx'];
    const extension = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!allowedTypes.includes(extension)) {
      alert('Tip de fișier nesuportat. Folosește .txt, .md, .pdf, .doc sau .docx');
      return;
    }

    setIsLoading(true);
    try {
      // Convert to base64
      const base64 = await fileToBase64(file);

      // Call backend to extract text
      const response = await fetch('/api/v1/documents/analyze', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          filename: file.name,
          content: base64,
          mime_type: file.type
        })
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      if (setUploadedDoc) {
        setUploadedDoc({ name: file.name, text: data.text });
      }
      if (onTextExtracted) {
        onTextExtracted(data.text);
      }

    } catch (err) {
      console.error(err);
      alert('Eroare la procesarea documentului. Verifică că backend-ul este pornit.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleRedFlags = async () => {
    const textToAnalyze = redFlagsTab === 'upload' && uploadedDocRedFlags
      ? uploadedDocRedFlags.text
      : redFlagsText;

    if (!textToAnalyze || textToAnalyze.trim().length < 10) {
      alert("Introduceți text pentru analiză (min. 10 caractere) sau încărcați un document.");
      return;
    }

    setIsLoading(true);
    setRedFlagsResults([]);
    setRedFlagsProgress("Se trimite documentul pentru analiză...");

    // Progress simulation — shows user what's happening during long analysis
    const charCount = textToAnalyze.length;
    const isLargeDoc = charCount > 15000;
    const progressTimer = setTimeout(() => {
      setRedFlagsProgress(
        isLargeDoc
          ? "Document mare detectat — se analizează în secțiuni paralele..."
          : "Se identifică clauzele problematice..."
      );
    }, 3000);
    const progressTimer2 = setTimeout(() => {
      setRedFlagsProgress("Se verifică cu legislația și jurisprudența CNSC...");
    }, isLargeDoc ? 20000 : 15000);
    const progressTimer3 = setTimeout(() => {
      setRedFlagsProgress("Se fundamentează fiecare red flag cu articole reale...");
    }, isLargeDoc ? 45000 : 30000);

    // AbortController with 180s timeout for large documents
    const controller = new AbortController();
    const timeoutMs = isLargeDoc ? 180000 : 120000;
    const fetchTimeout = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetch('/api/v1/redflags/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          text: textToAnalyze,
          use_jurisprudence: true
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => null);
        const detail = errorData?.detail || `Eroare server (${response.status})`;
        throw new Error(detail);
      }

      const data = await response.json();
      setRedFlagsResults(data.red_flags || []);

    } catch (err: any) {
      console.error(err);
      if (err.name === 'AbortError') {
        alert(
          `Analiza a depășit timpul limită (${timeoutMs / 1000}s). ` +
          'Documentul poate fi prea complex. Încearcă cu o secțiune mai mică.'
        );
      } else {
        alert(`Eroare la analiză: ${err.message || 'Verifică că backend-ul este pornit.'}`);
      }
    } finally {
      clearTimeout(progressTimer);
      clearTimeout(progressTimer2);
      clearTimeout(progressTimer3);
      clearTimeout(fetchTimeout);
      setIsLoading(false);
      setRedFlagsProgress("");
    }
  };

  const handleClarification = async () => {
    setIsLoading(true);
    setGeneratedContent("");
    setGeneratedDecisionRefs([]);
    try {
      const response = await fetch('/api/v1/clarification/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clause: clarificationClause })
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      setGeneratedContent(data.content || "");
      setGeneratedDecisionRefs(data.decision_refs || []);
    } catch (err) {
      console.error(err);
      setGeneratedContent("Eroare la generare. Verifică că backend-ul este pornit.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleRAGMemo = async () => {
    if (!memoTopic || memoTopic.trim().length < 3) {
      alert("Introduceți un topic pentru memo juridic (min. 3 caractere).");
      return;
    }

    setIsLoading(true);
    setGeneratedContent("");

    try {
      await fetchStream(
        '/api/v1/ragmemo/stream',
        {
          topic: memoTopic,
          max_decisions: 5,
        },
        (chunk) => {
          setGeneratedContent(prev => prev + chunk);
        },
        (_meta) => {
          // Memo content is already streamed, metadata not used in UI
        },
        (error) => {
          setGeneratedContent(`Eroare: ${error}`);
        },
      );
    } catch (err) {
      console.error(err);
      setGeneratedContent("Eroare la generarea memo-ului. Verifică că backend-ul este pornit și conectat la baza de date.");
    } finally {
      setIsLoading(false);
    }
  };


  // --- Render Functions ---

  const renderSidebar = () => (
    <div className="w-72 bg-slate-900 h-screen flex flex-col border-r border-slate-800 shrink-0 text-slate-300">
      <div className="p-6 border-b border-slate-800">
        <h1 className="text-2xl font-bold text-white flex items-center gap-2 tracking-tight">
          <div className="bg-blue-600 p-1.5 rounded-lg">
             <Database size={20} className="text-white" />
          </div>
          ExpertAP
        </h1>
        <p className="text-xs text-slate-500 mt-2 font-medium">Platformă de Business Intelligence <br/>pentru Achiziții Publice</p>
      </div>

      <nav className="flex-1 overflow-y-auto px-4 py-6 space-y-8">
        <div>
           <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 px-2">Workspace</div>
           <SidebarItem icon={LayoutDashboard} label="Dashboard" active={mode === 'dashboard'} onClick={() => setMode('dashboard')} />
           <SidebarItem icon={Database} label="Data Lake" active={mode === 'datalake'} onClick={() => setMode('datalake')} badge={files.length} />
           <SidebarItem icon={MessageSquare} label="Asistent AI" active={mode === 'chat'} onClick={() => setMode('chat')} />
        </div>

        <div>
           <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 px-2">Instrumente Juridice</div>
           <SidebarItem icon={Scale} label="Drafter Contestații" active={mode === 'drafter'} onClick={() => setMode('drafter')} />
           <SidebarItem icon={AlertTriangle} label="Red Flags Detector" active={mode === 'redflags'} onClick={() => setMode('redflags')} />
           <SidebarItem icon={Search} label="Clarificări" active={mode === 'clarification'} onClick={() => setMode('clarification')} />
           <SidebarItem icon={BookOpen} label="Jurisprudență RAG" active={mode === 'rag'} onClick={() => setMode('rag')} />
        </div>

        <div>
           <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 px-2">Formare</div>
           <SidebarItem icon={GraduationCap} label="TrainingAP" active={mode === 'training'} onClick={() => setMode('training')} />
        </div>
      </nav>

      <div className="p-4 border-t border-slate-800 bg-slate-900/50">
         <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-blue-500 to-purple-500 flex items-center justify-center text-white font-bold text-xs">AI</div>
            <div>
               <p className="text-sm text-white font-medium">Gemini 3 Pro</p>
               <p className="text-xs text-green-400">System Operational</p>
            </div>
         </div>
      </div>
    </div>
  );

  const renderDashboard = () => {
    const totalDecisions = dbStats?.total_decisions || 0;
    const admisCount = (dbStats?.by_ruling?.['ADMIS'] || 0) + (dbStats?.by_ruling?.['ADMIS_PARTIAL'] || 0);
    const respinsCount = dbStats?.by_ruling?.['RESPINS'] || 0;
    const rezultatCount = dbStats?.by_type?.['rezultat'] || 0;
    const isConnected = dbStats !== null && totalDecisions > 0;

    return (
    <div className="p-8 max-w-6xl mx-auto animate-in fade-in duration-500">
      <header className="mb-8 flex justify-between items-center">
        <div>
           <h2 className="text-3xl font-bold text-slate-900">Dashboard</h2>
           <p className="text-slate-500">Bine ai venit în centrul de comandă ExpertAP.</p>
        </div>
        <div className="flex items-center gap-2 bg-white border border-slate-200 px-3 py-1.5 rounded-full shadow-sm">
           <div className={`w-2.5 h-2.5 rounded-full ${isConnected ? 'bg-green-500 animate-pulse' : dbStats === null ? 'bg-yellow-500 animate-pulse' : 'bg-slate-300'}`}></div>
           <span className="text-xs font-medium text-slate-600">
              {isConnected ? `Conectat: ${totalDecisions} decizii` : dbStats === null ? "Conectare..." : "Deconectat"}
           </span>
        </div>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-10">
         <StatCard
            label="Total Decizii CNSC"
            value={totalDecisions}
            icon={FileText}
            color="bg-blue-500 text-blue-600"
         />
         <StatCard
            label="Decizii Rezultat"
            value={rezultatCount}
            icon={Database}
            color="bg-purple-500 text-purple-600"
         />
         <StatCard
            label="Admise/Admis Parțial"
            value={admisCount}
            icon={CheckCircle}
            color="bg-teal-500 text-teal-600"
         />
         <StatCard
            label="Respinse"
            value={respinsCount}
            icon={XCircle}
            color="bg-red-500 text-red-600"
         />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
           <div className="flex justify-between items-center mb-6">
              <h3 className="font-bold text-slate-800 flex items-center gap-2">
                 <Database size={18} className="text-blue-500" />
                 Conexiune Database
              </h3>
              <span className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded border border-slate-200 font-mono">
                PostgreSQL Cloud SQL
              </span>
           </div>

           {dbStats === null ? (
             <div className="flex flex-col items-center justify-center p-10">
                <div className="w-14 h-14 rounded-full flex items-center justify-center mb-3 bg-blue-100">
                   <RefreshCw size={24} className="text-blue-600 animate-spin" />
                </div>
                <span className="text-slate-700 font-medium">Se încarcă statisticile...</span>
                <span className="text-xs text-slate-500 mt-1">Conectare la baza de date</span>
             </div>
           ) : isConnected ? (
             <div className="bg-green-50 border border-green-100 rounded-lg p-6 flex flex-col items-center text-center">
                <CheckCircle size={32} className="text-green-500 mb-2" />
                <h4 className="font-bold text-green-800">Conexiune Activă</h4>
                <p className="text-sm text-green-700 mt-1">
                   Conectat la baza de date. {totalDecisions} decizii CNSC disponibile.
                </p>
                <button onClick={() => setMode('datalake')} className="mt-4 text-sm bg-white border border-green-200 text-green-700 px-4 py-2 rounded-lg hover:bg-green-100 transition shadow-sm font-medium">
                   Explorează Deciziile
                </button>
             </div>
           ) : (
             <div className="bg-amber-50 border border-amber-100 rounded-lg p-6 flex flex-col items-center text-center">
                <Database size={32} className="text-amber-500 mb-2" />
                <h4 className="font-bold text-amber-800">Baza de Date Goală</h4>
                <p className="text-sm text-amber-700 mt-1">
                   Nu s-au găsit decizii în baza de date. Verifică conexiunea sau importă date.
                </p>
             </div>
           )}
        </div>

        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
           <h3 className="font-bold text-slate-800 mb-4 flex items-center gap-2">
              <CheckSquare size={18} className="text-green-500" />
              Jurisprudență Disponibilă
           </h3>
           <p className="text-sm text-slate-600 mb-4">
             Deciziile CNSC sunt disponibile pentru analiză AI în toate secțiunile aplicației.
             Vezi detalii complete în <strong>Data Lake</strong>.
           </p>

           <div className="space-y-3">
              {isConnected ? (
                <>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="bg-blue-50 border border-blue-100 rounded-lg p-3">
                      <div className="text-2xl font-bold text-blue-700">{totalDecisions}</div>
                      <div className="text-xs text-blue-600 mt-1">Total Decizii</div>
                    </div>
                    <div className="bg-green-50 border border-green-100 rounded-lg p-3">
                      <div className="text-2xl font-bold text-green-700">{admisCount}</div>
                      <div className="text-xs text-green-600 mt-1">Admise</div>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="bg-red-50 border border-red-100 rounded-lg p-3">
                      <div className="text-2xl font-bold text-red-700">{respinsCount}</div>
                      <div className="text-xs text-red-600 mt-1">Respinse</div>
                    </div>
                    <div className="bg-purple-50 border border-purple-100 rounded-lg p-3">
                      <div className="text-2xl font-bold text-purple-700">{rezultatCount}</div>
                      <div className="text-xs text-purple-600 mt-1">Rezultat</div>
                    </div>
                  </div>
                  <button onClick={() => setMode('datalake')} className="w-full text-sm bg-blue-500 text-white px-4 py-2 rounded-lg hover:bg-blue-600 transition shadow-sm font-medium">
                    Explorează Database
                  </button>
                </>
              ) : (
                <div className="text-sm text-amber-600 bg-amber-50 p-3 rounded border border-amber-100">
                   Nu există decizii în baza de date. AI-ul va răspunde doar din cunoștințe generale.
                </div>
              )}
           </div>
        </div>
      </div>
    </div>
    );
  };

  const renderDataLake = () => {
    const totalDecisions = dbStats?.total_decisions || 0;
    const documentatieCount = dbStats?.by_type?.['documentatie'] || 0;
    const rezultatCount = dbStats?.by_type?.['rezultat'] || 0;
    const totalPages = Math.ceil(apiDecisionsTotal / 20);

    const goToPage = (page: number) => {
      if (page >= 1 && page <= totalPages) {
        fetchDecisions(page, fileSearch);
      }
    };

    // Ruling badge helper
    const rulingBadge = (solutie: string | null) => {
      const label = solutie === 'ADMIS' ? 'Admis' :
                    solutie === 'ADMIS_PARTIAL' ? 'Admis Parțial' :
                    solutie === 'RESPINS' ? 'Respins' : solutie || 'N/A';
      const cls = solutie === 'ADMIS' ? 'bg-emerald-500 text-white' :
                  solutie === 'ADMIS_PARTIAL' ? 'bg-amber-500 text-white' :
                  solutie === 'RESPINS' ? 'bg-red-500 text-white' :
                  'bg-slate-400 text-white';
      return <span className={`text-[11px] px-2.5 py-0.5 rounded-full font-semibold tracking-wide ${cls}`}>{label}</span>;
    };

    return (
      <div className="h-full flex flex-col bg-slate-50/80">
        {/* Header */}
        <div className="px-6 pt-5 pb-4 bg-white border-b border-slate-200 shrink-0">
          <div className="flex justify-between items-center mb-4">
            <div>
              <h2 className="text-xl font-bold text-slate-800 flex items-center gap-2">
                <Database className="text-blue-600" size={22}/> Data Lake
              </h2>
              <div className="flex items-center gap-2 mt-1">
                <div className="flex items-center gap-1.5 bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded-full border border-emerald-200 text-[11px] font-medium">
                  <Wifi size={11} />
                  Conectat
                </div>
                <span className="text-[11px] px-2 py-0.5 rounded-full font-medium bg-blue-50 text-blue-600 border border-blue-200">
                  PostgreSQL
                </span>
              </div>
            </div>
          </div>

          {/* Stats Cards */}
          <div className="grid grid-cols-4 gap-3">
            <div className="bg-gradient-to-br from-blue-50 to-blue-100/50 rounded-xl p-3.5 border border-blue-200/60">
              <div className="text-2xl font-extrabold text-blue-700 tracking-tight">{totalDecisions.toLocaleString()}</div>
              <div className="text-[11px] text-blue-600/80 font-medium mt-0.5">Total Decizii</div>
            </div>
            <div className="bg-gradient-to-br from-purple-50 to-purple-100/50 rounded-xl p-3.5 border border-purple-200/60">
              <div className="text-2xl font-extrabold text-purple-700 tracking-tight">{documentatieCount.toLocaleString()}</div>
              <div className="text-[11px] text-purple-600/80 font-medium mt-0.5">Documentație</div>
            </div>
            <div className="bg-gradient-to-br from-orange-50 to-orange-100/50 rounded-xl p-3.5 border border-orange-200/60">
              <div className="text-2xl font-extrabold text-orange-700 tracking-tight">{rezultatCount.toLocaleString()}</div>
              <div className="text-[11px] text-orange-600/80 font-medium mt-0.5">Rezultat</div>
            </div>
            <div className="bg-gradient-to-br from-emerald-50 to-emerald-100/50 rounded-xl p-3.5 border border-emerald-200/60">
              <div className="text-2xl font-extrabold text-emerald-700 tracking-tight">{dbStats?.last_updated ? new Date(dbStats.last_updated).toLocaleDateString('ro-RO') : '-'}</div>
              <div className="text-[11px] text-emerald-600/80 font-medium mt-0.5">Ultima actualizare</div>
            </div>
          </div>
        </div>

        {/* Search + Filters Bar */}
        <div className="px-6 py-3 border-b border-slate-200 bg-white shrink-0">
          <div className="flex items-center gap-4">
            {/* Search */}
            <div className="relative flex-1">
              <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" size={16} />
              <input
                type="text"
                className="w-full pl-10 pr-4 py-2 border border-slate-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500/40 focus:border-blue-400 focus:shadow-inner outline-none transition placeholder:text-slate-400"
                placeholder="Caută după număr decizie, autoritate, CPV..."
                value={fileSearch}
                onChange={(e) => setFileSearch(e.target.value)}
              />
            </div>
            {/* Dropdowns */}
            <div className="flex items-center gap-2">
              <div className="relative">
                <select value={filterRuling} onChange={(e) => setFilterRuling(e.target.value)} className="appearance-none text-xs border border-slate-300 rounded-lg pl-3 pr-7 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 focus:border-blue-400 outline-none transition min-w-[120px] cursor-pointer">
                  <option value="">Soluție: Toate</option>
                  <option value="ADMIS">Admis</option>
                  <option value="ADMIS_PARTIAL">Admis Parțial</option>
                  <option value="RESPINS">Respins</option>
                </select>
                <ChevronDown size={13} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
              </div>
              <div className="relative">
                <select value={filterType} onChange={(e) => setFilterType(e.target.value)} className="appearance-none text-xs border border-slate-300 rounded-lg pl-3 pr-7 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 focus:border-blue-400 outline-none transition min-w-[120px] cursor-pointer">
                  <option value="">Tip: Toate</option>
                  <option value="documentatie">Documentație</option>
                  <option value="rezultat">Rezultat</option>
                </select>
                <ChevronDown size={13} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
              </div>
              <div className="relative">
                <select value={filterYear} onChange={(e) => setFilterYear(e.target.value)} className="appearance-none text-xs border border-slate-300 rounded-lg pl-3 pr-7 py-2 bg-white text-slate-700 focus:ring-2 focus:ring-blue-500/40 focus:border-blue-400 outline-none transition min-w-[120px] cursor-pointer">
                  <option value="">An: Toate</option>
                  {Array.from({length: 6}, (_, i) => 2026 - i).map(y => (
                    <option key={y} value={String(y)}>{y}</option>
                  ))}
                </select>
                <ChevronDown size={13} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
              </div>
            </div>
            {(filterRuling || filterType || filterYear || fileSearch) && (
              <button onClick={() => { setFilterRuling(""); setFilterType(""); setFilterYear(""); setFileSearch(""); }}
                className="text-xs text-blue-600 hover:text-blue-800 font-medium whitespace-nowrap flex items-center gap-1 transition">
                <X size={13} /> Resetează
              </button>
            )}
          </div>
        </div>

        {/* Decision Cards */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {isLoadingDecisions ? (
            <div className="flex flex-col items-center justify-center py-20">
              <Loader2 size={32} className="text-blue-500 animate-spin mb-3" />
              <span className="text-sm text-slate-500">Se încarcă deciziile...</span>
            </div>
          ) : (
          <div className="space-y-3">
            {apiDecisions.map((dec: any) => {
              const snippet = dec.argumentatie_cnsc_snippet || dec.rezumat || '';
              const firstCritiqueCode = dec.coduri_critici?.[0];
              const critiqueDesc = firstCritiqueCode ? CRITIQUE_LEGEND[firstCritiqueCode] || '' : '';

              return (
                <div key={dec.id}
                  className="group bg-white rounded-xl border border-slate-200/80 hover:border-blue-300 hover:shadow-md transition-all cursor-pointer flex items-stretch"
                  onClick={() => openDecision(`BO${dec.an_bo}_${dec.numar_bo}`)}
                >
                  {/* Main content area */}
                  <div className="flex-1 p-4 min-w-0">
                    {/* Row 1: ID + Status Badge + Date */}
                    <div className="flex items-center gap-2.5 mb-2">
                      <span className="text-sm font-bold text-slate-900 font-mono tracking-tight">
                        BO{dec.an_bo}_{dec.numar_bo}
                      </span>
                      {rulingBadge(dec.solutie_contestatie)}
                      {dec.data_decizie && (
                        <span className="text-[11px] text-slate-400 font-medium">
                          {new Date(dec.data_decizie).toLocaleDateString('ro-RO')}
                        </span>
                      )}
                    </div>

                    {/* Row 2: CPV */}
                    <p className="text-xs text-slate-500 mb-1.5 truncate">
                      <span className="font-semibold text-slate-600">{dec.cod_cpv || 'N/A'}</span>
                      {dec.cpv_descriere && <span className="text-slate-400"> — {dec.cpv_descriere}</span>}
                    </p>

                    {/* Row 3: Argumentație CNSC snippet */}
                    {snippet && (
                      <p className="text-xs text-slate-400 leading-relaxed line-clamp-2 mb-3">
                        {snippet}
                      </p>
                    )}

                    {/* Row 4: Tag Footer — Type + Critique codes + descriptions */}
                    <div className="flex items-center gap-2 flex-wrap">
                      {/* Type pill */}
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                        dec.tip_contestatie === 'documentatie'
                          ? 'bg-purple-50 text-purple-600 border border-purple-200'
                          : 'bg-orange-50 text-orange-600 border border-orange-200'
                      }`}>
                        {dec.tip_contestatie === 'documentatie' ? 'Documentație' : 'Rezultat'}
                      </span>
                      {/* Critique code pills + description */}
                      {dec.coduri_critici?.map((cod: string) => (
                        <span key={cod} className="text-[10px] bg-slate-100 text-slate-600 px-2 py-0.5 rounded-full font-mono font-semibold border border-slate-200">
                          {cod}
                        </span>
                      ))}
                      {critiqueDesc && (
                        <span className="text-[10px] text-slate-400 truncate max-w-[400px]">
                          {critiqueDesc}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Right action: Eye icon */}
                  <div className="flex items-center justify-center px-5 border-l border-slate-100 group-hover:border-blue-100 transition-colors shrink-0">
                    <Eye size={22} className="text-slate-300 group-hover:text-blue-500 transition-colors" />
                  </div>
                </div>
              );
            })}

            {apiDecisions.length === 0 && (
              <div className="text-center py-20 text-slate-400 flex flex-col items-center">
                <div className="w-20 h-20 bg-slate-100 rounded-full flex items-center justify-center mb-4">
                  <Database size={32} className="text-slate-300" />
                </div>
                <h3 className="text-lg font-medium text-slate-600 mb-1">
                  {totalDecisions === 0 ? 'Baza de date este goală' : 'Nu s-au găsit rezultate'}
                </h3>
                <p className="max-w-md mx-auto text-sm">
                  {totalDecisions === 0
                    ? 'Nu există decizii CNSC în baza de date. Importă decizii pentru a începe.'
                    : 'Încearcă o altă căutare sau modifică filtrele.'}
                </p>
              </div>
            )}
          </div>
          )}
        </div>

        {/* Pagination */}
        <div className="bg-white px-6 py-3 border-t border-slate-200 text-xs text-slate-500 flex justify-between items-center shrink-0">
          <span className="font-medium">Pagina {apiDecisionsPage} din {totalPages || 1} <span className="text-slate-400">({apiDecisionsTotal.toLocaleString()} decizii)</span></span>
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => goToPage(apiDecisionsPage - 1)}
              disabled={apiDecisionsPage <= 1}
              className="px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-30 disabled:cursor-not-allowed text-xs font-medium transition"
            >
              Anterior
            </button>
            {totalPages > 0 && Array.from({length: Math.min(5, totalPages)}, (_, i) => {
              let page: number;
              if (totalPages <= 5) {
                page = i + 1;
              } else if (apiDecisionsPage <= 3) {
                page = i + 1;
              } else if (apiDecisionsPage >= totalPages - 2) {
                page = totalPages - 4 + i;
              } else {
                page = apiDecisionsPage - 2 + i;
              }
              return (
                <button
                  key={page}
                  onClick={() => goToPage(page)}
                  className={`w-8 h-8 rounded-lg text-xs font-medium transition ${
                    page === apiDecisionsPage
                      ? 'bg-blue-600 text-white shadow-sm'
                      : 'border border-slate-200 hover:bg-slate-50 text-slate-600'
                  }`}
                >
                  {page}
                </button>
              );
            })}
            <button
              onClick={() => goToPage(apiDecisionsPage + 1)}
              disabled={apiDecisionsPage >= totalPages}
              className="px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-30 disabled:cursor-not-allowed text-xs font-medium transition"
            >
              Următor
            </button>
          </div>
          <div className="flex items-center gap-1.5 text-emerald-600 font-medium">
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-500"></div>
            Connected
          </div>
        </div>
      </div>
    );
  };

  const renderDrafter = () => (
    <div className="h-full flex flex-col md:flex-row bg-white">
      <div className="w-full md:w-1/3 border-r border-slate-200 p-6 overflow-y-auto bg-slate-50/50">
        <h2 className="text-lg font-bold text-slate-800 mb-6 flex gap-2 items-center">
          <Scale className="text-blue-600" size={20}/> 
          Configurare Contestație
        </h2>
        
        <div className="space-y-5">
          <div className="bg-slate-50 p-4 rounded-lg border border-dashed border-slate-300">
            <label className="text-xs font-bold text-slate-500 uppercase mb-2 block">
              Încarcă document (.txt, .md, .pdf)
            </label>
            <input
              type="file"
              accept=".txt,.md,.pdf,.doc,.docx"
              onChange={(e) => handleDocumentUpload(e, (text) => setDrafterContext(prev => ({...prev, facts: text})), setUploadedDocDrafter)}
              className="block w-full text-sm text-slate-600
                file:mr-4 file:py-1.5 file:px-3
                file:rounded-lg file:border-0
                file:text-xs file:font-semibold
                file:bg-blue-50 file:text-blue-700
                hover:file:bg-blue-100"
            />
            {uploadedDocDrafter && (
              <p className="text-xs text-green-600 mt-2">
                ✓ {uploadedDocDrafter.name} ({uploadedDocDrafter.text.length} caractere)
              </p>
            )}
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Situația de Fapt</label>
            <textarea
              className={`w-full p-3 border rounded-lg text-sm h-32 focus:ring-2 focus:ring-blue-500 outline-none transition shadow-sm ${drafterContext.facts.length > 200000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
              placeholder="Descrie cronologia evenimentelor sau încarcă un document..."
              value={drafterContext.facts}
              onChange={(e) => setDrafterContext({...drafterContext, facts: e.target.value})}
            />
            <CharCounter value={drafterContext.facts} maxLength={200000} />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Argumentele Autorității</label>
            <textarea
              className={`w-full p-3 border rounded-lg text-sm h-32 focus:ring-2 focus:ring-blue-500 outline-none transition shadow-sm ${drafterContext.authorityArgs.length > 200000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
              placeholder="Ce motive a invocat autoritatea pentru respingere?"
              value={drafterContext.authorityArgs}
              onChange={(e) => setDrafterContext({...drafterContext, authorityArgs: e.target.value})}
            />
            <CharCounter value={drafterContext.authorityArgs} maxLength={200000} />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Temei Legal</label>
            <input
              type="text"
              className={`w-full p-3 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none transition shadow-sm ${drafterContext.legalGrounds.length > 50000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
              placeholder="Ex: Art. 215 Legea 98/2016"
              value={drafterContext.legalGrounds}
              onChange={(e) => setDrafterContext({...drafterContext, legalGrounds: e.target.value})}
            />
            <CharCounter value={drafterContext.legalGrounds} maxLength={50000} />
          </div>
          
          <button 
            onClick={handleDrafting}
            disabled={isLoading}
            className="w-full bg-slate-900 text-white py-4 rounded-xl font-medium hover:bg-slate-800 transition flex justify-center items-center gap-2 shadow-lg hover:shadow-xl mt-4"
          >
            {isLoading ? <Loader2 className="animate-spin" /> : "Generează Proiect"}
          </button>
        </div>
      </div>
      
      <div className="w-full md:w-2/3 p-10 overflow-y-auto bg-white">
        {generatedContent ? (
          <div className="max-w-3xl mx-auto">
             <div className="flex justify-end mb-4">
                <button className="text-sm text-blue-600 font-medium hover:underline">Descarcă .DOCX</button>
             </div>
             <div className="prose prose-slate max-w-none font-serif text-slate-800 leading-loose bg-white" dangerouslySetInnerHTML={{ __html: formatMarkdown(generatedContent) }} />
             {generatedDecisionRefs.length > 0 && (
               <div className="mt-6 p-4 bg-blue-50 border border-blue-200 rounded-xl">
                 <p className="font-semibold text-slate-700 mb-2 text-sm">📚 Jurisprudență CNSC utilizată:</p>
                 <div className="flex flex-wrap gap-2">
                   {generatedDecisionRefs.map((ref: string) => (
                     <span key={ref} className="text-xs bg-white text-blue-700 px-3 py-1.5 rounded-lg border border-blue-200 font-mono cursor-pointer hover:bg-blue-100 transition" onClick={() => openDecision(ref)}>{ref}</span>
                   ))}
                 </div>
               </div>
             )}
          </div>
        ) : (
          <div className="h-full flex flex-col items-center justify-center text-slate-300">
            <Scale size={64} className="mb-6 opacity-20" />
            <p className="text-lg font-medium">Configurează parametrii contestației</p>
            <p className="text-sm mt-2">AI-ul va genera structura juridică completă.</p>
          </div>
        )}
      </div>
    </div>
  );

  const trainingMaterialTypes: Record<string, { name: string; desc: string }> = {
    speta: { name: "Speță practică", desc: "Scenariu realist cu analiză juridică" },
    studiu_caz: { name: "Studiu de caz", desc: "Analiză aprofundată, multiple perspective" },
    situational: { name: "Întrebări situaționale", desc: "Scenarii decizionale 'Ce ați face dacă...'" },
    palarii: { name: "Pălăriile Gânditoare", desc: "6 perspective (de Bono)" },
    dezbatere: { name: "Dezbatere Pro & Contra", desc: "Argumente pro/contra cu temei legal" },
    quiz: { name: "Quiz cu variante", desc: "Întrebări cu răspunsuri multiple A/B/C/D" },
    joc_rol: { name: "Joc de rol", desc: "Scenarii cu roluri și instrucțiuni" },
    erori: { name: "Identificare erori", desc: "Document cu greșeli de identificat" },
    comparativ: { name: "Analiză comparativă", desc: "Compararea a două abordări" },
    cronologie: { name: "Cronologie procedurală", desc: "Ordonarea pașilor unei proceduri" },
  };

  const buildTrainingRequestBody = (tipOverride?: string) => ({
    tema: trainingTema,
    tip_material: tipOverride || trainingTip,
    nivel_dificultate: trainingNivel,
    lungime: trainingLungime,
    context_suplimentar: trainingContext,
    public_tinta: trainingPublicTinta || undefined,
    program_plan: trainingMode === 'program' ? trainingProgramPlan : undefined,
  });

  // Toggle a material type in multi-select
  const toggleTrainingType = (key: string) => {
    setTrainingSelectedTypes(prev =>
      prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]
    );
  };

  const handleTrainingGenerate = async () => {
    if (!trainingTema.trim() || trainingLoading) return;

    const resetUI = () => {
      setTrainingLoading(true);
      setTrainingResult("");
      setTrainingMeta(null);
      setTrainingActiveTab('material');
      setTrainingEditing(false);
      setTrainingEditedResult(null);
    };

    // Program mode — LLM generates the full program
    if (trainingMode === 'program') {
      resetUI();

      await fetchStream(
        '/api/v1/training/generate/stream',
        {
          ...buildTrainingRequestBody('program_formare'),
          selected_types: trainingSelectedTypes.length > 0 ? trainingSelectedTypes : undefined,
        },
        (text) => setTrainingResult(prev => prev + text),
        (meta) => { setTrainingMeta(meta); setTrainingLoading(false); },
        (error) => { setTrainingResult(prev => prev + `\n\n**Eroare:** ${error}`); setTrainingLoading(false); },
      );
      return;
    }

    // Batch mode — generate multiple materials sequentially, streaming each into view
    if (trainingMode === 'batch' && trainingBatchCount > 1) {
      resetUI();
      setTrainingBatchProgress({ current: 0, total: trainingBatchCount, results: [] });

      // Determine type sequence: cycle through selected types
      const typesToUse = trainingSelectedTypes.length > 0 ? trainingSelectedTypes : [trainingTip];

      for (let i = 0; i < trainingBatchCount; i++) {
        const tipForThis = typesToUse[i % typesToUse.length];
        const tipName = trainingMaterialTypes[tipForThis]?.name || tipForThis;
        setTrainingBatchProgress(prev => prev ? { ...prev, current: i + 1 } : null);

        // Add header for this material before streaming begins
        const header = `\n\n---\n\n# Material ${i + 1} din ${trainingBatchCount} — ${tipName}\n\n`;
        setTrainingResult(prev => prev + header);

        await fetchStream(
          '/api/v1/training/generate/stream',
          {
            ...buildTrainingRequestBody(tipForThis),
            batch_index: i + 1,
            batch_total: trainingBatchCount,
          },
          // Stream each chunk directly into the visible result
          (text) => setTrainingResult(prev => prev + text),
          () => {},
          (error) => { setTrainingResult(prev => prev + `\n\n**Eroare:** ${error}`); },
        );
      }

      setTrainingBatchProgress(null);
      setTrainingLoading(false);
      return;
    }

    // Individual mode (default)
    resetUI();

    await fetchStream(
      '/api/v1/training/generate/stream',
      buildTrainingRequestBody(),
      (text) => setTrainingResult(prev => prev + text),
      (meta) => { setTrainingMeta(meta); setTrainingLoading(false); },
      (error) => { setTrainingResult(prev => prev + `\n\n**Eroare:** ${error}`); setTrainingLoading(false); },
    );
  };

  const handleTrainingExport = async (format: 'docx' | 'pdf' | 'md') => {
    const contentToExport = trainingEditedResult ?? trainingResult;
    if (!contentToExport) return;
    try {
      const response = await fetch('/api/v1/training/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: contentToExport,
          format,
          titlu: `TrainingAP - ${trainingMaterialTypes[trainingTip]?.name || trainingTip}`,
          metadata: trainingMeta,
        }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const ext = format === 'docx' ? 'docx' : format === 'pdf' ? 'pdf' : 'md';
      a.download = `TrainingAP_${trainingTip}_${trainingNivel}.${ext}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Export failed:', err);
    }
  };

  const parseTrainingSections = (text: string) => {
    const sections: Record<string, string> = {};
    let currentKey: string | null = null;
    let currentLines: string[] = [];

    for (const line of text.split('\n')) {
      const lower = line.trim().toLowerCase();
      if (lower.startsWith('## enun') || lower.startsWith('## cerin')) {
        if (currentKey) sections[currentKey] = currentLines.join('\n').trim();
        currentKey = lower.startsWith('## enun') ? 'material' : 'material';
        if (lower.startsWith('## cerin')) currentKey = 'material';
        // Keep Enunț and Cerințe together in "material" tab
        currentLines.push(line);
        continue;
      } else if (lower.startsWith('## rezolv')) {
        if (currentKey) sections[currentKey] = currentLines.join('\n').trim();
        currentKey = 'rezolvare';
        currentLines = [];
        continue;
      } else if (lower.startsWith('## note')) {
        if (currentKey) sections[currentKey] = currentLines.join('\n').trim();
        currentKey = 'note';
        currentLines = [];
        continue;
      }
      currentLines.push(line);
    }
    if (currentKey) sections[currentKey] = currentLines.join('\n').trim();

    // If no sections parsed, put everything in material
    if (!sections.material && !sections.rezolvare && !sections.note) {
      sections.material = text;
    }

    return sections;
  };

  const renderTraining = () => {
    const effectiveResult = trainingEditedResult ?? trainingResult;
    const sections = effectiveResult ? parseTrainingSections(effectiveResult) : {};
    const activeContent = sections[trainingActiveTab] || '';

    return (
      <div className="h-full flex flex-col md:flex-row bg-white">
        {/* Left panel — form */}
        <div className="w-full md:w-1/3 border-r border-slate-200 p-6 overflow-y-auto bg-slate-50/50">
          <h2 className="text-lg font-bold text-slate-800 mb-6 flex gap-2 items-center">
            <GraduationCap className="text-amber-600" size={20}/>
            TrainingAP — Materiale Didactice
          </h2>

          <div className="space-y-5">
            {/* Mode selector */}
            <div>
              <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Mod Generare</label>
              <div className="grid grid-cols-3 gap-2">
                {[
                  { key: 'individual' as const, label: 'Individual', desc: '1 material' },
                  { key: 'batch' as const, label: 'Lot (Batch)', desc: 'Mai multe' },
                  { key: 'program' as const, label: 'Program Formare', desc: 'Complet' },
                ].map(({ key, label, desc }) => (
                  <button
                    key={key}
                    onClick={() => setTrainingMode(key)}
                    className={`py-2 px-2 rounded-lg text-xs font-medium transition border text-center ${
                      trainingMode === key
                        ? 'bg-amber-600 text-white border-amber-600 shadow-md'
                        : 'bg-white text-slate-600 border-slate-300 hover:border-amber-400'
                    }`}
                  >
                    <div>{label}</div>
                    <div className="opacity-70 text-[10px]">{desc}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* Tema */}
            <div>
              <label className="block text-xs font-bold text-slate-700 uppercase mb-2">
                {trainingMode === 'program' ? 'Tema Programului de Formare' : 'Tema / Subiectul'}
              </label>
              <textarea
                className={`w-full p-3 border rounded-lg text-sm h-24 focus:ring-2 focus:ring-amber-500 outline-none transition shadow-sm ${trainingTema.length > 20000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                placeholder={trainingMode === 'program'
                  ? "Ex: Program de formare pentru evaluarea ofertelor — 4 module, 2 zile..."
                  : "Ex: Evaluarea ofertelor în procedura de licitație deschisă, Termenele de contestare, Criteriul prețul cel mai scăzut vs. cel mai bun raport calitate-preț..."}
                value={trainingTema}
                onChange={(e) => setTrainingTema(e.target.value)}
              />
              <CharCounter value={trainingTema} maxLength={20000} />
            </div>

            {/* Public țintă (opțional) */}
            <div>
              <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Public Țintă (opțional)</label>
              <textarea
                className={`w-full p-3 border rounded-lg text-sm h-16 focus:ring-2 focus:ring-amber-500 outline-none transition shadow-sm ${trainingPublicTinta.length > 5000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                placeholder="Ex: Reprezentanți autorități contractante, Reprezentanți operatori economici, Reprezentanți autoritatea de audit / Curtea de Conturi / organe de control, Reprezentanți CNSC, Consultanți achiziții publice..."
                value={trainingPublicTinta}
                onChange={(e) => setTrainingPublicTinta(e.target.value)}
              />
              {trainingPublicTinta.length > 100 && <CharCounter value={trainingPublicTinta} maxLength={5000} />}
            </div>

            {/* Tip material — single select for individual, multi-select for batch/program */}
            {trainingMode === 'individual' ? (
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Tip Material</label>
                <select
                  className="w-full p-3 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-amber-500 outline-none transition shadow-sm bg-white"
                  value={trainingTip}
                  onChange={(e) => setTrainingTip(e.target.value)}
                >
                  {Object.entries(trainingMaterialTypes).map(([key, val]) => (
                    <option key={key} value={key}>{val.name} — {val.desc}</option>
                  ))}
                </select>
              </div>
            ) : (
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-2">
                  Tipuri Materiale {trainingMode === 'batch' ? '(se alternează)' : '(selectează tipurile dorite)'}
                </label>
                <div className="grid grid-cols-2 gap-1.5">
                  {Object.entries(trainingMaterialTypes).map(([key, val]) => {
                    const isSelected = trainingSelectedTypes.includes(key);
                    return (
                      <button
                        key={key}
                        onClick={() => toggleTrainingType(key)}
                        className={`flex items-center gap-2 py-1.5 px-2.5 rounded-lg text-xs font-medium transition border text-left ${
                          isSelected
                            ? 'bg-amber-50 text-amber-700 border-amber-300'
                            : 'bg-white text-slate-500 border-slate-200 hover:border-amber-300'
                        }`}
                      >
                        <span className={`w-3.5 h-3.5 rounded border flex items-center justify-center shrink-0 ${
                          isSelected ? 'bg-amber-500 border-amber-500 text-white' : 'border-slate-300'
                        }`}>
                          {isSelected && <CheckSquare size={10} />}
                        </span>
                        <span className="truncate">{val.name}</span>
                      </button>
                    );
                  })}
                </div>
                {trainingSelectedTypes.length === 0 && (
                  <p className="text-[10px] text-amber-600 mt-1">
                    {trainingMode === 'batch'
                      ? 'Selectează cel puțin un tip. Se va folosi tipul implicit.'
                      : 'Dacă nu selectezi nimic, LLM-ul va alege automat.'}
                  </p>
                )}
              </div>
            )}

            {/* Batch count — only in batch mode */}
            {trainingMode === 'batch' && (
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Număr Materiale</label>
                <div className="flex items-center gap-2">
                  {!trainingBatchCustom ? (
                    <select
                      className="flex-1 p-3 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-amber-500 outline-none transition shadow-sm bg-white"
                      value={trainingBatchCount}
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v === 'custom') { setTrainingBatchCustom(true); }
                        else setTrainingBatchCount(Number(v));
                      }}
                    >
                      {Array.from({ length: 10 }, (_, i) => i + 1).map(n => (
                        <option key={n} value={n}>{n} {n === 1 ? 'material' : 'materiale'}</option>
                      ))}
                      <option value="custom">Altă valoare...</option>
                    </select>
                  ) : (
                    <div className="flex-1 flex items-center gap-2">
                      <input
                        type="number"
                        min={1}
                        max={50}
                        className="flex-1 p-3 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-amber-500 outline-none transition shadow-sm"
                        value={trainingBatchCount}
                        onChange={(e) => setTrainingBatchCount(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
                      />
                      <button
                        onClick={() => setTrainingBatchCustom(false)}
                        className="text-xs text-amber-600 hover:text-amber-800 font-medium whitespace-nowrap"
                      >
                        ← Lista
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Program plan — only in program mode */}
            {trainingMode === 'program' && (
              <div>
                <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Plan / Program de Formare (opțional)</label>
                <div className="bg-slate-50 p-3 rounded-lg border border-dashed border-slate-300 mb-2">
                  <input
                    type="file"
                    accept=".txt,.md,.pdf,.doc,.docx"
                    onChange={(e) => handleDocumentUpload(e, (text) => setTrainingProgramPlan(text), setUploadedDocTrainingPlan)}
                    className="block w-full text-sm text-slate-600 file:mr-4 file:py-1 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-amber-50 file:text-amber-700 hover:file:bg-amber-100"
                  />
                  {uploadedDocTrainingPlan && (
                    <p className="text-xs text-green-600 mt-1">
                      ✓ {uploadedDocTrainingPlan.name} ({uploadedDocTrainingPlan.text.length.toLocaleString()} car.)
                    </p>
                  )}
                </div>
                <textarea
                  className={`w-full p-3 border rounded-lg text-sm h-24 focus:ring-2 focus:ring-amber-500 outline-none transition shadow-sm ${trainingProgramPlan.length > 50000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                  placeholder="Lipește sau descrie programul de formare: module, tematici, competențe vizate, durată..."
                  value={trainingProgramPlan}
                  onChange={(e) => setTrainingProgramPlan(e.target.value)}
                />
                {trainingProgramPlan.length > 100 && <CharCounter value={trainingProgramPlan} maxLength={50000} />}
                <p className="text-[10px] text-slate-400 mt-1">LLM-ul va alege automat cele mai potrivite tipuri de materiale pentru fiecare tematică din program.</p>
              </div>
            )}

            {/* Nivel dificultate */}
            <div>
              <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Nivel Dificultate</label>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { key: 'usor', label: 'Ușor' },
                  { key: 'mediu', label: 'Mediu' },
                  { key: 'dificil', label: 'Dificil' },
                  { key: 'foarte_dificil', label: 'Foarte Dificil' },
                ].map(({ key, label }) => (
                  <button
                    key={key}
                    onClick={() => setTrainingNivel(key)}
                    className={`py-2 px-3 rounded-lg text-sm font-medium transition border ${
                      trainingNivel === key
                        ? 'bg-amber-600 text-white border-amber-600 shadow-md'
                        : 'bg-white text-slate-600 border-slate-300 hover:border-amber-400 hover:text-amber-700'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {/* Lungime */}
            <div>
              <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Lungime Material</label>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { key: 'scurt', label: 'Scurt', desc: '~200 cuv./secț.' },
                  { key: 'mediu', label: 'Mediu', desc: '~400 cuv./secț.' },
                  { key: 'lung', label: 'Lung', desc: '~800 cuv./secț.' },
                  { key: 'extins', label: 'Extins', desc: '~1500 cuv./secț.' },
                ].map(({ key, label, desc }) => (
                  <button
                    key={key}
                    onClick={() => setTrainingLungime(key)}
                    className={`py-2 px-3 rounded-lg text-xs font-medium transition border ${
                      trainingLungime === key
                        ? 'bg-amber-600 text-white border-amber-600 shadow-md'
                        : 'bg-white text-slate-600 border-slate-300 hover:border-amber-400 hover:text-amber-700'
                    }`}
                  >
                    {label} <span className="opacity-70">({desc})</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Context suplimentar (collapsible) */}
            <div>
              <button
                onClick={() => setTrainingShowContext(!trainingShowContext)}
                className="flex items-center gap-2 text-xs font-bold text-slate-500 uppercase hover:text-slate-700 transition"
              >
                {trainingShowContext ? <ChevronUp size={14}/> : <ChevronDown size={14}/>}
                Context suplimentar (opțional)
              </button>
              {trainingShowContext && (
                <div>
                  <div className="bg-slate-50 p-3 rounded-lg border border-dashed border-slate-300 mt-2 mb-2">
                    <input
                      type="file"
                      accept=".txt,.md,.pdf,.doc,.docx"
                      onChange={(e) => handleDocumentUpload(e, (text) => setTrainingContext(text), setUploadedDocTrainingContext)}
                      className="block w-full text-sm text-slate-600 file:mr-4 file:py-1 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-amber-50 file:text-amber-700 hover:file:bg-amber-100"
                    />
                    {uploadedDocTrainingContext && (
                      <p className="text-xs text-green-600 mt-1">
                        ✓ {uploadedDocTrainingContext.name} ({uploadedDocTrainingContext.text.length.toLocaleString()} car.)
                      </p>
                    )}
                  </div>
                  <textarea
                    className={`w-full p-3 border rounded-lg text-sm h-20 focus:ring-2 focus:ring-amber-500 outline-none transition shadow-sm ${trainingContext.length > 50000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                    placeholder="Instrucțiuni adiționale, restricții, focus pe anumite aspecte..."
                    value={trainingContext}
                    onChange={(e) => setTrainingContext(e.target.value)}
                  />
                  <CharCounter value={trainingContext} maxLength={50000} />
                </div>
              )}
            </div>

            {/* Generate button */}
            <button
              onClick={handleTrainingGenerate}
              disabled={trainingLoading || !trainingTema.trim()}
              className="w-full bg-amber-600 text-white py-4 rounded-xl font-medium hover:bg-amber-700 transition flex justify-center items-center gap-2 shadow-lg hover:shadow-xl mt-2 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {trainingLoading ? (
                <>
                  <Loader2 className="animate-spin" size={18} />
                  {trainingBatchProgress
                    ? `Material ${trainingBatchProgress.current} / ${trainingBatchProgress.total}...`
                    : 'Generare în curs...'}
                </>
              ) : (
                <>
                  <GraduationCap size={18} />
                  {trainingMode === 'batch' ? `Generează ${trainingBatchCount} Materiale` :
                   trainingMode === 'program' ? 'Generează Program Complet' :
                   'Generează Material'}
                </>
              )}
            </button>
          </div>
        </div>

        {/* Right panel — output */}
        <div className="w-full md:w-2/3 flex flex-col overflow-hidden bg-white">
          {trainingResult ? (
            <>
              {/* Toolbar */}
              <div className="border-b border-slate-200 px-6 py-3 flex items-center justify-between bg-slate-50/50">
                {/* Tabs */}
                <div className="flex gap-1">
                  {[
                    { key: 'material' as const, label: 'Enunț & Cerințe' },
                    { key: 'rezolvare' as const, label: 'Rezolvare' },
                    { key: 'note' as const, label: 'Note Trainer' },
                  ].map(({ key, label }) => (
                    <button
                      key={key}
                      onClick={() => setTrainingActiveTab(key)}
                      className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
                        trainingActiveTab === key
                          ? 'bg-amber-600 text-white shadow-sm'
                          : 'text-slate-600 hover:bg-slate-200'
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>

                {/* Edit toggle + Export buttons */}
                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      if (!trainingEditing) {
                        // Entering edit mode — initialize edited text from current result
                        if (trainingEditedResult === null) {
                          setTrainingEditedResult(trainingResult);
                        }
                      }
                      setTrainingEditing(!trainingEditing);
                    }}
                    disabled={trainingLoading}
                    className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border transition disabled:opacity-50 ${
                      trainingEditing
                        ? 'bg-amber-100 border-amber-400 text-amber-700'
                        : 'border-slate-300 text-slate-600 hover:bg-slate-100 hover:border-slate-400'
                    }`}
                  >
                    {trainingEditing ? <Eye size={12} /> : <Pencil size={12} />}
                    {trainingEditing ? 'Previzualizare' : 'Editează'}
                  </button>
                  {(['docx', 'pdf', 'md'] as const).map((fmt) => (
                    <button
                      key={fmt}
                      onClick={() => handleTrainingExport(fmt)}
                      disabled={trainingLoading}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-100 hover:border-slate-400 transition disabled:opacity-50"
                    >
                      <Download size={12} />
                      {fmt.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>

              {/* Content */}
              <div className="flex-1 overflow-y-auto p-8">
                <div className="max-w-3xl mx-auto">
                  {trainingEditing ? (
                    <textarea
                      value={trainingEditedResult ?? trainingResult}
                      onChange={(e) => setTrainingEditedResult(e.target.value)}
                      className="w-full h-full min-h-[500px] p-4 text-sm font-mono border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-amber-400 focus:border-transparent resize-y bg-slate-50 leading-relaxed"
                      placeholder="Editează materialul generat..."
                    />
                  ) : activeContent ? (
                    <div
                      className="prose prose-slate max-w-none leading-relaxed"
                      dangerouslySetInnerHTML={{ __html: formatMarkdown(activeContent) }}
                    />
                  ) : trainingLoading ? (
                    <div className="flex items-center gap-3 text-slate-500">
                      <Loader2 className="animate-spin" size={18} />
                      <span className="text-sm">Se generează materialul...</span>
                    </div>
                  ) : (
                    <p className="text-slate-400 text-sm italic">Această secțiune nu a fost generată.</p>
                  )}
                </div>
              </div>

              {/* Footer — citations */}
              {trainingMeta && (trainingMeta.jurisprudenta_citata?.length > 0 || trainingMeta.legislatie_citata?.length > 0) && (
                <div className="border-t border-slate-200 px-6 py-3 bg-slate-50/50">
                  <div className="flex flex-wrap gap-4">
                    {trainingMeta.jurisprudenta_citata?.length > 0 && (
                      <div>
                        <span className="text-xs font-bold text-slate-500 uppercase mr-2">Jurisprudență:</span>
                        <span className="inline-flex flex-wrap gap-1">
                          {trainingMeta.jurisprudenta_citata.map((ref: string) => (
                            <span key={ref} className="text-xs bg-blue-50 text-blue-700 px-2 py-1 rounded border border-blue-200 font-mono cursor-pointer hover:bg-blue-100 transition" onClick={() => openDecision(ref)}>{ref}</span>
                          ))}
                        </span>
                      </div>
                    )}
                    {trainingMeta.legislatie_citata?.length > 0 && (
                      <div>
                        <span className="text-xs font-bold text-slate-500 uppercase mr-2">Legislație:</span>
                        <span className="inline-flex flex-wrap gap-1">
                          {trainingMeta.legislatie_citata.map((ref: string, i: number) => (
                            <span key={i} className="text-xs bg-green-50 text-green-700 px-2 py-1 rounded border border-green-200">{ref}</span>
                          ))}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="h-full flex flex-col items-center justify-center text-slate-300">
              <GraduationCap size={64} className="mb-6 opacity-20" />
              <p className="text-lg font-medium text-slate-400">Configurează parametrii materialului</p>
              <p className="text-sm mt-2 text-slate-300 max-w-md text-center">
                Alege tema, tipul de material, nivelul de dificultate și lungimea dorită.
                AI-ul va genera materialul fundamentat pe legislația și jurisprudența reală din baza de date.
              </p>
            </div>
          )}
        </div>
      </div>
    );
  };

  const renderChat = () => (
    <div className="flex flex-col h-full bg-white">
      <div className="border-b border-slate-100 p-4 flex justify-between items-center bg-white">
         <h2 className="font-bold text-slate-800 flex items-center gap-2">
            <MessageSquare className="text-blue-500" size={18} /> 
            ExpertAP Chat
         </h2>
         <span className="text-xs text-slate-500 bg-slate-100 px-2 py-1 rounded">🗄️ Conectat la baza de date CNSC</span>
      </div>
      <div className="flex-1 overflow-y-auto p-6 space-y-6 bg-slate-50/50">
        {chatMessages.length === 0 && (
           <div className="text-center text-slate-400 mt-20">
             <div className="w-16 h-16 bg-white rounded-2xl shadow-sm flex items-center justify-center mx-auto mb-6">
                <MessageSquare size={32} className="text-blue-500" />
             </div>
             <h3 className="text-slate-800 font-bold mb-2">Cu ce te pot ajuta astăzi?</h3>
             <p className="text-sm max-w-md mx-auto">Pot răspunde la întrebări despre deciziile CNSC din baza de date sau despre legislația în achiziții publice.</p>
           </div>
        )}
        {chatMessages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-2xl p-5 shadow-sm ${
              msg.role === 'user'
                ? 'bg-slate-900 text-white rounded-br-none'
                : 'bg-white border border-slate-200 text-slate-800 rounded-bl-none prose prose-slate max-w-none'
            }`}>
              {msg.role === 'user' ? msg.text : (
                <div dangerouslySetInnerHTML={{ __html: formatMarkdown(msg.text) }} />
              )}
            </div>
          </div>
        ))}
        {isLoading && (
          <div className="flex justify-start">
             <div className="bg-white border border-slate-200 p-4 rounded-2xl rounded-bl-none shadow-sm flex gap-3 items-center">
               <Loader2 size={18} className="animate-spin text-blue-600" />
               <span className="text-sm text-slate-500 font-medium">Analizez informațiile...</span>
             </div>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>
      <div className="p-4 bg-white border-t border-slate-200">
        <div className="max-w-4xl mx-auto">
          <div className="flex gap-2 relative">
            <input
              type="text"
              className={`flex-1 border rounded-xl pl-5 pr-12 py-4 focus:ring-2 focus:ring-blue-500 outline-none shadow-sm ${chatInput.length > 100000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
              placeholder="Scrie mesajul tău..."
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleChat()}
            />
            <button
              onClick={handleChat}
              disabled={isLoading || !chatInput.trim()}
              className="absolute right-2 top-2 bottom-2 bg-blue-600 text-white px-4 rounded-lg hover:bg-blue-700 disabled:opacity-50 transition flex items-center justify-center"
            >
              <Send size={18} />
            </button>
          </div>
          {chatInput.length > 1000 && <CharCounter value={chatInput} maxLength={100000} />}
        </div>
        <p className="text-center text-xs text-slate-400 mt-2">Gemini Pro poate face greșeli. Verifică informațiile importante.</p>
      </div>
    </div>
  );

  return (
    <div className="flex h-screen bg-slate-50 font-sans text-slate-900">
      {renderSidebar()}
      <main className="flex-1 overflow-hidden relative shadow-2xl z-10 rounded-l-2xl border-l border-slate-200/50 bg-white ml-[-1px]">
        {mode === 'dashboard' && renderDashboard()}
        {mode === 'datalake' && renderDataLake()}
        {mode === 'drafter' && renderDrafter()}
        {mode === 'chat' && renderChat()}
        {mode === 'redflags' && (
          <div className="h-full flex flex-col md:flex-row bg-white">
            {/* Left panel — input */}
            <div className="w-full md:w-1/3 border-r border-slate-200 p-6 overflow-y-auto bg-slate-50/50">
              <h2 className="text-lg font-bold text-slate-800 mb-2 flex gap-2 items-center">
                <AlertTriangle className="text-red-500" size={20}/> Red Flags Detector
              </h2>
              <p className="text-xs text-slate-500 mb-6">Identifică clauze restrictive în documentația de achiziții publice.</p>

              {/* Tabs */}
              <div className="flex gap-1 mb-4 border-b border-slate-200">
                <button
                  onClick={() => setRedFlagsTab('manual')}
                  className={`px-3 py-2 text-sm font-medium transition border-b-2 ${
                    redFlagsTab === 'manual'
                      ? 'border-red-600 text-red-600'
                      : 'border-transparent text-slate-500 hover:text-slate-700'
                  }`}
                >
                  Manual Input
                </button>
                <button
                  onClick={() => setRedFlagsTab('upload')}
                  className={`px-3 py-2 text-sm font-medium transition border-b-2 ${
                    redFlagsTab === 'upload'
                      ? 'border-red-600 text-red-600'
                      : 'border-transparent text-slate-500 hover:text-slate-700'
                  }`}
                >
                  Upload Document
                </button>
              </div>

              {/* Manual Input Tab */}
              {redFlagsTab === 'manual' && (
                <div className="space-y-4">
                  <div>
                    <label className="block text-xs font-bold text-slate-700 uppercase mb-2">
                      Documentație Achiziție
                    </label>
                    <textarea
                      className={`w-full p-3 border rounded-lg h-48 text-sm focus:ring-2 focus:ring-red-500 outline-none transition shadow-sm font-mono ${redFlagsText.length > 200000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                      placeholder="Introduceți sau lipiți conținutul documentației..."
                      value={redFlagsText}
                      onChange={(e) => setRedFlagsText(e.target.value)}
                    />
                    <CharCounter value={redFlagsText} maxLength={200000} />
                  </div>
                  <button
                    onClick={handleRedFlags}
                    disabled={isLoading || !redFlagsText.trim()}
                    className="w-full bg-red-600 text-white py-3 rounded-xl font-medium hover:bg-red-700 transition disabled:opacity-50 flex items-center gap-2 justify-center shadow-lg hover:shadow-xl"
                  >
                    {isLoading ? <Loader2 className="animate-spin" size={18} /> : <AlertTriangle size={18} />}
                    {isLoading ? 'Analizare în curs...' : 'Analizează Red Flags'}
                  </button>
                  {isLoading && redFlagsProgress && (
                    <p className="text-sm text-slate-500 text-center animate-pulse">
                      {redFlagsProgress}
                    </p>
                  )}
                </div>
              )}

              {/* Upload Document Tab */}
              {redFlagsTab === 'upload' && (
                <div className="space-y-4">
                  <div className="bg-slate-50 p-4 rounded-lg border border-dashed border-slate-300">
                    <label className="text-xs font-bold text-slate-500 uppercase mb-2 block">
                      Încarcă Document (.txt, .md, .pdf)
                    </label>
                    <input
                      type="file"
                      accept=".txt,.md,.pdf,.doc,.docx"
                      onChange={(e) => handleDocumentUpload(e, (text) => setRedFlagsText(text), setUploadedDocRedFlags)}
                      className="block w-full text-sm text-slate-600
                        file:mr-4 file:py-1.5 file:px-3
                        file:rounded-lg file:border-0
                        file:text-xs file:font-semibold
                        file:bg-red-50 file:text-red-700
                        hover:file:bg-red-100"
                    />
                    {uploadedDocRedFlags && (
                      <p className="text-xs text-green-600 mt-2">
                        ✓ {uploadedDocRedFlags.name} ({uploadedDocRedFlags.text.length} caractere)
                      </p>
                    )}
                  </div>
                  <button
                    onClick={handleRedFlags}
                    disabled={isLoading || !uploadedDocRedFlags}
                    className="w-full bg-red-600 text-white py-3 rounded-xl font-medium hover:bg-red-700 transition disabled:opacity-50 flex items-center gap-2 justify-center shadow-lg hover:shadow-xl"
                  >
                    {isLoading ? <Loader2 className="animate-spin" size={18} /> : <AlertTriangle size={18} />}
                    {isLoading ? 'Analizare în curs...' : 'Analizează Red Flags'}
                  </button>
                  {isLoading && redFlagsProgress && (
                    <p className="text-sm text-slate-500 text-center animate-pulse">
                      {redFlagsProgress}
                    </p>
                  )}
                </div>
              )}
            </div>

            {/* Right panel — results */}
            <div className="w-full md:w-2/3 p-8 overflow-y-auto bg-white">
              {redFlagsResults.length > 0 ? (
                <div className="space-y-4">
                  <div className="bg-white p-4 rounded-lg border border-slate-200 flex items-center justify-between sticky top-0 z-10">
                    <h3 className="font-bold text-slate-800">Rezultate Analiză</h3>
                    <div className="flex gap-4 text-sm">
                      <span className="text-red-600 font-bold">
                        {redFlagsResults.filter(rf => rf.severity === 'CRITICĂ').length} Critice
                      </span>
                      <span className="text-orange-600 font-bold">
                        {redFlagsResults.filter(rf => rf.severity === 'MEDIE').length} Medii
                      </span>
                      <span className="text-yellow-600 font-bold">
                        {redFlagsResults.filter(rf => rf.severity === 'SCĂZUTĂ').length} Scăzute
                      </span>
                    </div>
                  </div>

                  {redFlagsResults.map((flag, idx) => (
                    <div
                      key={idx}
                      className={`bg-white p-6 rounded-xl border-l-4 shadow-sm ${
                        flag.severity === 'CRITICĂ'
                          ? 'border-red-500'
                          : flag.severity === 'MEDIE'
                          ? 'border-orange-500'
                          : 'border-yellow-500'
                      }`}
                    >
                      <div className="flex items-start justify-between mb-3">
                        <div className="flex items-center gap-2">
                          <AlertTriangle
                            className={
                              flag.severity === 'CRITICĂ'
                                ? 'text-red-500'
                                : flag.severity === 'MEDIE'
                                ? 'text-orange-500'
                                : 'text-yellow-500'
                            }
                            size={20}
                          />
                          <span
                            className={`text-xs px-3 py-1 rounded-full font-bold ${
                              flag.severity === 'CRITICĂ'
                                ? 'bg-red-100 text-red-700'
                                : flag.severity === 'MEDIE'
                                ? 'bg-orange-100 text-orange-700'
                                : 'bg-yellow-100 text-yellow-700'
                            }`}
                          >
                            {flag.severity}
                          </span>
                        </div>
                      </div>

                      <div className="space-y-3 text-sm">
                        <div>
                          <p className="font-semibold text-slate-700 mb-1">Clauza Problematica:</p>
                          <p className="bg-slate-50 p-3 rounded border border-slate-200 italic text-slate-600">
                            "{flag.clause}"
                          </p>
                        </div>

                        <div>
                          <p className="font-semibold text-slate-700 mb-1">Problema:</p>
                          <p className="text-slate-600">{flag.issue}</p>
                        </div>

                        {flag.legal_references && flag.legal_references.length > 0 && (
                          <div>
                            <p className="font-semibold text-slate-700 mb-1">Temei Legal:</p>
                            <div className="space-y-2">
                              {flag.legal_references.map((ref: any, refIdx: number) => (
                                <details key={refIdx} className="group">
                                  <summary className="cursor-pointer flex items-center gap-2 text-slate-700 hover:text-blue-700">
                                    <span className="font-mono text-xs bg-emerald-50 text-emerald-700 px-2 py-1 rounded border border-emerald-200">
                                      {ref.citare} din {ref.act_normativ}
                                    </span>
                                    <ChevronDown size={14} className="group-open:rotate-180 transition-transform text-slate-400" />
                                  </summary>
                                  {ref.text_extras && (
                                    <p className="mt-1 ml-2 p-2 bg-emerald-50/50 rounded text-xs text-slate-600 border-l-2 border-emerald-300">
                                      {ref.text_extras}
                                    </p>
                                  )}
                                </details>
                              ))}
                            </div>
                          </div>
                        )}

                        {flag.recommendation && (
                          <div>
                            <p className="font-semibold text-slate-700 mb-1">Recomandare:</p>
                            <p className="text-slate-600">{flag.recommendation}</p>
                          </div>
                        )}

                        {flag.decision_refs && flag.decision_refs.length > 0 && (
                          <div>
                            <p className="font-semibold text-slate-700 mb-1">Jurisprudenta CNSC:</p>
                            <div className="flex gap-2 flex-wrap">
                              {flag.decision_refs.map((ref: string) => (
                                <span
                                  key={ref}
                                  className="text-xs bg-blue-50 text-blue-700 px-2 py-1 rounded border border-blue-200 font-mono"
                                >
                                  {ref}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}

                        {(!flag.legal_references || flag.legal_references.length === 0) &&
                         (!flag.decision_refs || flag.decision_refs.length === 0) && (
                          <p className="text-xs text-slate-400 italic">
                            Nu s-au gasit referinte legislative sau jurisprudenta CNSC relevanta in baza de date
                          </p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-slate-300">
                  <AlertTriangle size={48} className="mb-4 opacity-20" />
                  <p className="text-lg font-medium">Rezultatele analizei vor apărea aici</p>
                  <p className="text-sm mt-2">
                    Introduceți text sau încărcați un document pentru a începe analiza
                  </p>
                </div>
              )}
            </div>
          </div>
        )}
        {mode === 'clarification' && handleClarification && (
          <div className="h-full flex flex-col md:flex-row bg-white">
            {/* Left panel — input */}
            <div className="w-full md:w-1/3 border-r border-slate-200 p-6 overflow-y-auto bg-slate-50/50">
              <h2 className="text-lg font-bold text-slate-800 mb-6 flex gap-2 items-center">
                <Search className="text-purple-600" size={20}/> Asistent Clarificări
              </h2>
              <div className="space-y-4">
                <div className="bg-slate-50 p-4 rounded-lg border border-dashed border-slate-300">
                  <label className="text-xs font-bold text-slate-500 uppercase mb-2 block">
                    Încarcă document (.txt, .md, .pdf)
                  </label>
                  <input
                    type="file"
                    accept=".txt,.md,.pdf,.doc,.docx"
                    onChange={(e) => handleDocumentUpload(e, (text) => setClarificationClause(text), setUploadedDocClarification)}
                    className="block w-full text-sm text-slate-600
                      file:mr-4 file:py-1.5 file:px-3
                      file:rounded-lg file:border-0
                      file:text-xs file:font-semibold
                      file:bg-purple-50 file:text-purple-700
                      hover:file:bg-purple-100"
                  />
                  {uploadedDocClarification && (
                    <p className="text-xs text-green-600 mt-2">
                      ✓ {uploadedDocClarification.name} ({uploadedDocClarification.text.length} caractere)
                    </p>
                  )}
                </div>
                <div>
                  <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Clauza Problematică</label>
                  <textarea
                    className={`w-full p-3 border rounded-lg text-sm h-32 focus:ring-2 focus:ring-purple-500 outline-none transition shadow-sm ${clarificationClause.length > 200000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                    placeholder="Paste text din documentație sau încarcă un document..."
                    value={clarificationClause}
                    onChange={(e) => setClarificationClause(e.target.value)}
                  />
                  <CharCounter value={clarificationClause} maxLength={200000} />
                </div>
                <button
                  onClick={handleClarification}
                  disabled={isLoading || !clarificationClause}
                  className="w-full bg-purple-600 text-white py-3 rounded-xl font-medium hover:bg-purple-700 transition flex justify-center items-center gap-2 shadow-lg hover:shadow-xl"
                >
                  {isLoading ? <Loader2 className="animate-spin" size={18} /> : "Generează Cerere Clarificare"}
                </button>
              </div>
            </div>
            {/* Right panel — output */}
            <div className="w-full md:w-2/3 p-10 overflow-y-auto bg-white">
              {generatedContent ? (
                <div>
                  <div className="prose prose-slate max-w-none" dangerouslySetInnerHTML={{ __html: formatMarkdown(generatedContent) }} />
                  {generatedDecisionRefs.length > 0 && (
                    <div className="mt-6 p-4 bg-purple-50 border border-purple-200 rounded-xl">
                      <p className="font-semibold text-slate-700 mb-2 text-sm">Jurisprudență CNSC utilizată:</p>
                      <div className="flex flex-wrap gap-2">
                        {generatedDecisionRefs.map((ref: string) => (
                          <span key={ref} className="text-xs bg-white text-purple-700 px-3 py-1.5 rounded-lg border border-purple-200 font-mono cursor-pointer hover:bg-purple-100 transition" onClick={() => openDecision(ref)}>{ref}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-slate-300">
                  <Search size={48} className="mb-4 opacity-20"/>
                  <p className="text-lg font-medium">Rezultatul va apărea aici</p>
                  <p className="text-sm mt-2">Încarcă un document sau introdu textul clauzei problematice</p>
                </div>
              )}
            </div>
          </div>
        )}
        {mode === 'rag' && handleRAGMemo && (
           <div className="h-full flex flex-col p-6">
              <header className="mb-6">
                 <h2 className="text-2xl font-bold text-slate-800 flex items-center gap-2"><BookOpen className="text-teal-600"/> Jurisprudență RAG</h2>
              </header>
              <div className="flex gap-6 h-full overflow-hidden">
                 <div className="w-80 shrink-0 flex flex-col gap-4">
                    <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
                       <div className="bg-slate-50 p-3 rounded-lg border border-dashed border-slate-300 mb-3">
                         <label className="text-xs font-bold text-slate-500 uppercase mb-2 block">
                           Încarcă document (.txt, .md, .pdf)
                         </label>
                         <input
                           type="file"
                           accept=".txt,.md,.pdf,.doc,.docx"
                           onChange={(e) => handleDocumentUpload(e, (text) => setMemoTopic(text), setUploadedDocRag)}
                           className="block w-full text-sm text-slate-600
                             file:mr-4 file:py-1.5 file:px-3
                             file:rounded-lg file:border-0
                             file:text-xs file:font-semibold
                             file:bg-teal-50 file:text-teal-700
                             hover:file:bg-teal-100"
                         />
                         {uploadedDocRag && (
                           <p className="text-xs text-green-600 mt-2">
                             ✓ {uploadedDocRag.name} ({uploadedDocRag.text.length} caractere)
                           </p>
                         )}
                       </div>
                       <label className="text-sm font-bold text-slate-700 block mb-2">Subiect Memo</label>
                       <textarea
                          className={`w-full border rounded-lg p-3 text-sm h-24 focus:ring-2 focus:ring-teal-500 outline-none ${memoTopic.length > 100000 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                          placeholder="Ex: Respingere ofertă sau încarcă document..."
                          value={memoTopic}
                          onChange={(e) => setMemoTopic(e.target.value)}
                       />
                       <CharCounter value={memoTopic} maxLength={100000} />
                       <button
                          onClick={handleRAGMemo}
                          disabled={isLoading || !memoTopic.trim()}
                          className="w-full bg-teal-600 text-white py-2.5 rounded-lg text-sm font-medium hover:bg-teal-700 transition disabled:opacity-50"
                       >
                          {isLoading ? "Analiză..." : "Generează Memo"}
                       </button>
                       <p className="text-xs text-slate-400 mt-3 text-center">Căutare semantică în {dbStats?.total_decisions || 0} decizii din baza de date.</p>
                    </div>
                 </div>
                 <div className="flex-1 bg-white border border-slate-200 rounded-xl shadow-sm p-8 overflow-y-auto text-slate-800 leading-relaxed">
                    {generatedContent ? (
                       <div className="prose prose-slate max-w-none" dangerouslySetInnerHTML={{ __html: formatMarkdown(generatedContent) }} />
                    ) : (
                       <div className="flex flex-col items-center justify-center h-full text-slate-300">
                          <BookOpen size={48} className="mb-4 opacity-20"/>
                          <p>Rezultatul RAG va apărea aici.</p>
                       </div>
                    )}
                 </div>
              </div>
           </div>
        )}
        {mode === 'training' && renderTraining()}
      </main>

      {/* Decision Viewer Modal */}
      {(viewingDecision || isLoadingDecision) && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => !isLoadingDecision && setViewingDecision(null)}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            {isLoadingDecision ? (
              <div className="flex items-center justify-center p-20">
                <Loader2 size={32} className="animate-spin text-blue-600" />
                <span className="ml-3 text-slate-600">Se încarcă decizia...</span>
              </div>
            ) : viewingDecision && (() => {
              // Compute match count for search navigation
              const searchActive = decisionSearchTerm && decisionSearchTerm.length >= 2;
              const safeSearch = searchActive ? decisionSearchTerm.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') : '';
              const matchCount = searchActive ? ((viewingDecision.content || "").match(new RegExp(safeSearch, 'gi')) || []).length : 0;

              return (
              <>
                {/* Header */}
                <div className="flex items-start justify-between gap-4 p-6 border-b border-slate-200 shrink-0">
                  <div className="min-w-0">
                    <h2 className="text-xl font-bold text-slate-900 font-mono">{viewingDecision.metadata?.case_number || viewingDecision.title}</h2>
                    <div className="flex gap-2 mt-2 flex-wrap">
                      {viewingDecision.metadata?.date && (
                        <span className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded border border-slate-200">
                          {new Date(viewingDecision.metadata.date).toLocaleDateString('ro-RO')}
                        </span>
                      )}
                      {viewingDecision.metadata?.ruling && (
                        <span className={`text-xs px-2 py-1 rounded border font-medium ${
                          viewingDecision.metadata.ruling === 'ADMIS' ? 'bg-green-50 text-green-700 border-green-200' :
                          viewingDecision.metadata.ruling === 'RESPINS' ? 'bg-red-50 text-red-700 border-red-200' :
                          'bg-yellow-50 text-yellow-700 border-yellow-200'
                        }`}>
                          {viewingDecision.metadata.ruling}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <div className="flex items-center border border-slate-200 rounded-lg overflow-hidden">
                      <div className="relative">
                        <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input
                          type="text"
                          placeholder="Caută în decizie..."
                          value={decisionSearchTerm}
                          onChange={(e) => setDecisionSearchTerm(e.target.value)}
                          className="pl-8 pr-2 py-1.5 text-sm w-44 focus:outline-none border-none"
                        />
                      </div>
                      {searchActive && (
                        <span className="text-xs text-slate-500 px-2 whitespace-nowrap tabular-nums">
                          {matchCount > 0 ? `${decisionSearchIndex + 1}/${matchCount}` : '0/0'}
                        </span>
                      )}
                      <button
                        onClick={() => { if (matchCount > 0) setDecisionSearchIndex(prev => prev <= 0 ? matchCount - 1 : prev - 1); }}
                        disabled={!searchActive || matchCount === 0}
                        className="p-1.5 hover:bg-slate-100 disabled:opacity-30 disabled:cursor-not-allowed border-l border-slate-200"
                      >
                        <ChevronUp size={14} className="text-slate-500" />
                      </button>
                      <button
                        onClick={() => { if (matchCount > 0) setDecisionSearchIndex(prev => prev >= matchCount - 1 ? 0 : prev + 1); }}
                        disabled={!searchActive || matchCount === 0}
                        className="p-1.5 hover:bg-slate-100 disabled:opacity-30 disabled:cursor-not-allowed border-l border-slate-200"
                      >
                        <ChevronDown size={14} className="text-slate-500" />
                      </button>
                      {decisionSearchTerm && (
                        <button
                          onClick={() => setDecisionSearchTerm("")}
                          className="p-1.5 hover:bg-slate-100 border-l border-slate-200"
                        >
                          <X size={14} className="text-slate-400" />
                        </button>
                      )}
                    </div>
                    <button
                      onClick={() => { setDecisionSearchTerm(""); setDecisionSearchIndex(0); setViewingDecision(null); }}
                      className="p-2 hover:bg-slate-100 rounded-lg transition text-slate-400 hover:text-slate-600 ml-1"
                    >
                      <X size={20} />
                    </button>
                  </div>
                </div>

                {/* Content */}
                <div className="flex-1 overflow-y-auto p-6" ref={decisionContentRef}>
                  <div
                    className="prose prose-slate max-w-none text-sm leading-relaxed whitespace-pre-wrap font-mono bg-slate-50 p-6 rounded-lg border border-slate-200"
                    dangerouslySetInnerHTML={{
                      __html: (() => {
                        const raw = viewingDecision.content || "";
                        const escaped = raw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                        if (!searchActive) return escaped;
                        const regex = new RegExp(`(${safeSearch})`, 'gi');
                        let idx = 0;
                        return escaped.replace(regex, (match: string) => {
                          const isActive = idx === decisionSearchIndex;
                          const style = isActive
                            ? 'background:#fb923c;color:white;padding:1px 2px;border-radius:2px'
                            : 'background:#fef08a;padding:1px 2px;border-radius:2px';
                          return `<mark data-match="${idx++}" style="${style}">${match}</mark>`;
                        });
                      })()
                    }}
                  />
                </div>

                {/* Footer */}
                <div className="p-4 border-t border-slate-200 flex justify-between items-center shrink-0">
                  <span className="text-xs text-slate-400">
                    {viewingDecision.content?.length?.toLocaleString()} caractere
                  </span>
                  <button
                    onClick={() => { setDecisionSearchTerm(""); setDecisionSearchIndex(0); setViewingDecision(null); }}
                    className="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200 transition text-sm font-medium"
                  >
                    Închide
                  </button>
                </div>
              </>
              );
            })()}
          </div>
        </div>
      )}
    </div>
  );
};

const root = createRoot(document.getElementById("root")!);
root.render(<App />);