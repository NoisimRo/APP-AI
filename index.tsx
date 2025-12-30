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
  FolderInput
} from "lucide-react";

// --- Types ---

type AppMode = 'dashboard' | 'datalake' | 'drafter' | 'redflags' | 'chat' | 'clarification' | 'rag';

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
  const [isLoadingDecisions, setIsLoadingDecisions] = useState(false);
  
  // Chat/Interaction States
  const [chatMessages, setChatMessages] = useState<Message[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [generatedContent, setGeneratedContent] = useState<string>("");

  // Specialized Input States
  const [drafterContext, setDrafterContext] = useState({ facts: "", authorityArgs: "", legalGrounds: "" });
  const [clarificationClause, setClarificationClause] = useState("");
  const [memoTopic, setMemoTopic] = useState("");

  const chatEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const ai = new GoogleGenAI({ apiKey });

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages, generatedContent]);

  useEffect(() => {
    setGeneratedContent("");
  }, [mode]);

  // Fetch decisions from API on mount
  useEffect(() => {
    const fetchDecisions = async () => {
      setIsLoadingDecisions(true);
      try {
        const response = await fetch('/api/v1/decisions/?limit=100');
        if (response.ok) {
          const data = await response.json();
          setApiDecisions(data.decisions || []);
        }
      } catch (error) {
        console.error('Failed to fetch decisions:', error);
      } finally {
        setIsLoadingDecisions(false);
      }
    };
    fetchDecisions();
  }, []);

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

    try {
      const contextParts = getActiveContextParts();
      
      const contents = [
        ...chatMessages.map(m => ({ role: m.role, parts: [{ text: m.text }] })),
        { 
          role: 'user', 
          parts: [
            ...contextParts, 
            { text: userMsg }
          ] 
        }
      ];

      const response = await ai.models.generateContent({
        model: 'gemini-3-flash-preview',
        contents: contents,
        config: {
          systemInstruction: "Ești ExpertAP, un consultant senior în achiziții publice. Răspunde concis, citând legislația din România (Legea 98/2016). Folosește contextul documentelor atașate dacă există."
        }
      });
      
      setChatMessages(prev => [...prev, { role: 'model', text: response.text || "" }]);
    } catch (err) {
      console.error(err);
      setChatMessages(prev => [...prev, { role: 'model', text: "Eroare la procesarea cererii. Verifică dimensiunea fișierelor active." }]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleDrafting = async () => {
    setIsLoading(true);
    setGeneratedContent("");
    
    try {
      const prompt = `
        Ești un avocat expert în achiziții publice. Redactează o contestație către CNSC.
        
        Detalii faptice: ${drafterContext.facts}
        Argumente Autoritate: ${drafterContext.authorityArgs}
        Temei legal: ${drafterContext.legalGrounds}
        
        Structura obligatorie:
        1. Părți.
        2. Situația de fapt.
        3. Motivele contestației (Dezvoltare amplă).
        4. Suspendare.
        5. Dispozitiv.
      `;

      const response = await ai.models.generateContent({
        model: 'gemini-3-pro-preview',
        contents: prompt,
        config: {
          thinkingConfig: { thinkingBudget: 4096 }
        }
      });
      setGeneratedContent(response.text || "");
    } catch (err) {
      setGeneratedContent("Eroare la generare.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleRedFlags = async () => {
    if (activeFiles.length === 0) {
      alert("Selectează cel puțin un fișier activ (Caiet de Sarcini) din Data Lake.");
      return;
    }
    setIsLoading(true);
    setGeneratedContent("");

    try {
      const parts = [
        ...getActiveContextParts(),
        {
          text: `
        Analizează documentația atașată. Identifică "Steaguri Roșii" (clauze restrictive/ilegale).
        Pentru fiecare:
        1. Clauza originală.
        2. Riscul juridic.
        3. Strategie de "Imunizare" (Clarificare propusă).
        
        Gândește profund la implicațiile subtile ale specificațiilor tehnice.
      ` }
      ];

      const response = await ai.models.generateContent({
        model: 'gemini-3-pro-preview',
        contents: { parts },
        config: {
          thinkingConfig: { thinkingBudget: 2048 }
        }
      });
      setGeneratedContent(response.text || "");
    } catch (err) {
      setGeneratedContent("Eroare la analiza documentelor.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleClarification = async () => {
    setIsLoading(true);
    setGeneratedContent("");
    try {
      const response = await ai.models.generateContent({
        model: 'gemini-3-flash-preview',
        contents: `
          Clientul vrea să conteste/clarifice această clauză: "${clarificationClause}".
          Redactează o Cerere de Clarificare formală, politicoasă, dar care sugerează subtil nelegalitatea cerinței.
        `
      });
      setGeneratedContent(response.text || "");
    } catch (err) {
      setGeneratedContent("Eroare la generare.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleRAGMemo = async () => {
    if (activeFiles.length === 0) {
      alert("Selectează fișiere relevante din Data Lake pentru a genera memo-ul.");
      return;
    }
    setIsLoading(true);
    setGeneratedContent("");

    try {
      const parts = [
        ...getActiveContextParts(),
        { text: `
        Folosind DOAR documentele atașate ca jurisprudență:
        Redactează un Memo Juridic pe tema: "${memoTopic}".
        Analizează cum s-a pronunțat CNSC în cazurile atașate și estimează șansele de succes.
      `}
      ];

      const response = await ai.models.generateContent({
        model: 'gemini-3-pro-preview',
        contents: { parts },
        config: {
           thinkingConfig: { thinkingBudget: 4096 } 
        }
      });
      setGeneratedContent(response.text || "");
    } catch (err) {
      setGeneratedContent("Eroare la generarea memo-ului.");
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

  const renderDashboard = () => (
    <div className="p-8 max-w-6xl mx-auto animate-in fade-in duration-500">
      <header className="mb-8 flex justify-between items-center">
        <div>
           <h2 className="text-3xl font-bold text-slate-900">Dashboard</h2>
           <p className="text-slate-500">Bine ai venit în centrul de comandă ExpertAP.</p>
        </div>
        <div className="flex items-center gap-2 bg-white border border-slate-200 px-3 py-1.5 rounded-full shadow-sm">
           <div className={`w-2.5 h-2.5 rounded-full ${apiDecisions.length > 0 ? 'bg-green-500 animate-pulse' : isLoadingDecisions ? 'bg-yellow-500 animate-pulse' : 'bg-slate-300'}`}></div>
           <span className="text-xs font-medium text-slate-600">
              {apiDecisions.length > 0 ? `Conectat: ${apiDecisions.length} decizii` : isLoadingDecisions ? "Conectare..." : "Deconectat"}
           </span>
        </div>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-10">
         <StatCard
            label="Total Decizii CNSC"
            value={apiDecisions.length}
            icon={FileText}
            color="bg-blue-500 text-blue-600"
         />
         <StatCard
            label="Decizii Rezultat"
            value={apiDecisions.filter(d => d.tip_contestatie === 'rezultat').length}
            icon={Database}
            color="bg-purple-500 text-purple-600"
         />
         <StatCard
            label="Admise/Admis Parțial"
            value={apiDecisions.filter(d => d.solutie_contestatie?.includes('ADMIS')).length}
            icon={CheckCircle}
            color="bg-teal-500 text-teal-600"
         />
         <StatCard
            label="Respinse"
            value={apiDecisions.filter(d => d.solutie_contestatie === 'RESPINS').length}
            icon={XCircle}
            color="bg-red-500 text-red-600"
         />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
           <div className="flex justify-between items-center mb-6">
              <h3 className="font-bold text-slate-800 flex items-center gap-2">
                 <Cloud size={18} className="text-blue-500" />
                 Conexiune Data Lake
              </h3>
              <span className="text-xs bg-slate-100 text-slate-600 px-2 py-1 rounded border border-slate-200 font-mono">
                s3://date-ap-raw/decizii-cnsc
              </span>
           </div>
           
           {files.length === 0 ? (
             <div 
                onClick={simulateSync}
                className="block w-full border-2 border-dashed border-slate-300 rounded-lg p-10 text-center hover:bg-slate-50 transition cursor-pointer group"
             >
                <div className="flex flex-col items-center">
                  <div className={`w-14 h-14 rounded-full flex items-center justify-center mb-3 transition ${isSyncing ? 'bg-blue-100' : 'bg-slate-50 group-hover:bg-blue-50'}`}>
                     {isSyncing ? (
                       <RefreshCw size={24} className="text-blue-600 animate-spin" />
                     ) : (
                       <Cloud size={24} className="text-slate-400 group-hover:text-blue-500" />
                     )}
                  </div>
                  <span className="text-slate-700 font-medium">
                    {isSyncing ? "Se stabilește conexiunea..." : "Conectare la Bucket"}
                  </span>
                  <span className="text-xs text-slate-500 mt-1 max-w-xs">
                     Sincronizează datele locale cu instanța ExpertAP. Click pentru a selecta directorul sursă.
                  </span>
                </div>
             </div>
           ) : (
             <div className="bg-green-50 border border-green-100 rounded-lg p-6 flex flex-col items-center text-center">
                <CheckCircle size={32} className="text-green-500 mb-2" />
                <h4 className="font-bold text-green-800">Conexiune Activă</h4>
                <p className="text-sm text-green-700 mt-1">
                   S-au încărcat {files.length} documente din bucket-ul <span className="font-mono text-xs">decizii-cnsc</span>.
                </p>
                <button onClick={() => setMode('datalake')} className="mt-4 text-sm bg-white border border-green-200 text-green-700 px-4 py-2 rounded-lg hover:bg-green-100 transition shadow-sm font-medium">
                   Vezi Fișierele
                </button>
             </div>
           )}
           
           {/* Hidden Input specifically for Folder Upload */}
           <input 
              type="file" 
              ref={fileInputRef}
              className="hidden" 
              onChange={handleFileUpload} 
              accept=".txt,.pdf"
              // @ts-ignore - React doesn't fully type webkitdirectory yet
              webkitdirectory=""
              directory=""
              multiple 
           />
        </div>

        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
           <h3 className="font-bold text-slate-800 mb-4 flex items-center gap-2">
              <CheckSquare size={18} className="text-green-500" />
              Status Context Activ
           </h3>
           <p className="text-sm text-slate-600 mb-4">
             Fișierele active sunt trimise automat către modelele AI pentru analiză.
             Gestionează selecția în secțiunea <strong>Data Lake</strong>.
           </p>
           
           <div className="space-y-3">
              {activeFiles.slice(0, 5).map(f => (
                <div key={f.id} className="flex items-center gap-2 text-sm text-slate-700 p-2 bg-slate-50 rounded">
                   <FileText size={14} className="text-slate-400" />
                   <span className="truncate flex-1">{f.name}</span>
                   <span className="text-xs bg-green-100 text-green-700 px-1.5 rounded">Activ</span>
                </div>
              ))}
              {activeFiles.length > 5 && (
                <div className="text-xs text-center text-slate-500 pt-2">
                   + încă {activeFiles.length - 5} fișiere active
                </div>
              )}
              {activeFiles.length === 0 && (
                <div className="text-sm text-amber-600 bg-amber-50 p-3 rounded border border-amber-100">
                   Niciun fișier activ. AI-ul va răspunde doar din cunoștințe generale.
                </div>
              )}
           </div>
        </div>
      </div>
    </div>
  );

  const renderDataLake = () => (
    <div className="h-full flex flex-col bg-slate-50">
      <div className="p-6 border-b border-slate-200 bg-white shrink-0">
        <div className="flex justify-between items-start mb-4">
            <div>
              <h2 className="text-xl font-bold text-slate-800 flex items-center gap-2">
                <Database className="text-blue-600" /> Data Lake
              </h2>
              <div className="flex items-center gap-2 mt-1">
                 <div className="flex items-center gap-1.5 bg-blue-50 text-blue-700 px-2 py-0.5 rounded border border-blue-100 text-xs font-mono">
                    <Wifi size={12} />
                    s3://date-ap-raw/decizii-cnsc
                 </div>
                 <span className={`text-xs px-2 py-0.5 rounded font-medium ${files.length > 0 ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-500'}`}>
                    {files.length > 0 ? 'Online' : 'Offline'}
                 </span>
              </div>
            </div>
            
            <div className="flex gap-2">
               <button 
                  onClick={simulateSync}
                  className="flex items-center gap-2 bg-slate-900 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-slate-800 transition shadow-sm"
               >
                 {isUploading ? <Loader2 size={16} className="animate-spin"/> : <RefreshCw size={16} />}
                 {isUploading ? "Se încarcă..." : "Sincronizare Bucket"}
               </button>
            </div>
        </div>
        
        {/* Status Bar */}
        <div className="bg-slate-50 rounded-lg p-3 text-xs text-slate-500 flex gap-4 border border-slate-100">
           <span>Total Obiecte: <span className="font-bold text-slate-700">{files.length}</span></span>
           <span>Dimensiune: <span className="font-bold text-slate-700">{(files.reduce((acc, f) => acc + f.content.length, 0) / 1024 / 1024).toFixed(2)} MB</span> (Load Memory)</span>
           <span>Actualizat: <span className="font-bold text-slate-700">Acum</span></span>
        </div>
      </div>

      <div className="p-4 border-b border-slate-200 bg-white flex items-center gap-4 shrink-0">
         <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
            <input 
              type="text" 
              className="w-full pl-10 pr-4 py-2 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
              placeholder="Filtrează după nume, an sau metadate..."
              value={fileSearch}
              onChange={(e) => setFileSearch(e.target.value)}
            />
         </div>
         <div className="flex items-center gap-2 text-sm text-slate-600 border-l pl-4 border-slate-200">
            <button onClick={() => toggleAllActive(true)} className="hover:text-blue-600 px-2 py-1 font-medium">Selectează Tot</button>
            <button onClick={() => toggleAllActive(false)} className="hover:text-red-600 px-2 py-1 font-medium">Deselectează Tot</button>
         </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6 relative">
        {isUploading && (
           <div className="absolute inset-0 bg-white/80 z-10 flex flex-col items-center justify-center backdrop-blur-sm">
              <Loader2 size={48} className="text-blue-600 animate-spin mb-4" />
              <h3 className="text-lg font-bold text-slate-800">Se procesează datele...</h3>
              <p className="text-slate-500">Se încarcă fișierele din bucket-ul local.</p>
           </div>
        )}
        
        <div className="grid grid-cols-1 gap-2">
          {filteredFiles.map((file) => (
            <div key={file.id} className={`group flex items-center justify-between p-3 rounded-lg border transition-all ${
              file.isActive 
                ? "bg-blue-50/50 border-blue-200 shadow-sm" 
                : "bg-white border-slate-200 hover:border-slate-300"
            }`}>
              <div className="flex items-center gap-4 flex-1 min-w-0">
                <button onClick={() => toggleFileActive(file.id)} className="text-slate-400 hover:text-blue-600 transition">
                  {file.isActive ? <CheckSquare className="text-blue-600" /> : <Square />}
                </button>
                <div className={`p-2 rounded text-slate-500 ${file.type.includes('pdf') ? 'bg-red-50 text-red-500' : 'bg-slate-50'}`}>
                  <FileText size={20} />
                </div>
                <div className="min-w-0">
                  <p className={`text-sm font-medium truncate ${file.isActive ? 'text-blue-900' : 'text-slate-700'}`}>
                    {file.name}
                  </p>
                  <div className="flex gap-2 mt-0.5 items-center">
                     <span className="text-[10px] text-slate-400 font-mono hidden md:inline-block">/decizii-cnsc/</span>
                    {file.metadata?.year && <span className="text-[10px] bg-slate-100 px-1.5 rounded text-slate-500 border border-slate-200">{file.metadata.year}</span>}
                    {file.metadata?.ruling === 'Admis' && <span className="text-[10px] bg-green-100 text-green-700 px-1.5 rounded border border-green-200 font-medium">Admis</span>}
                    {file.metadata?.ruling === 'Respins' && <span className="text-[10px] bg-red-100 text-red-700 px-1.5 rounded border border-red-200 font-medium">Respins</span>}
                  </div>
                </div>
              </div>
              
              <div className="flex items-center gap-3 pl-4">
                 <span className="text-xs text-slate-400 font-mono">{(file.content.length / 1024).toFixed(1)} KB</span>
                 <button onClick={() => removeFile(file.id)} className="p-2 text-slate-300 hover:text-red-500 hover:bg-red-50 rounded-full transition opacity-0 group-hover:opacity-100">
                    <Trash2 size={16} />
                 </button>
              </div>
            </div>
          ))}
          
          {filteredFiles.length === 0 && !isUploading && (
             <div className="text-center py-20 text-slate-400 flex flex-col items-center">
                <div className="w-20 h-20 bg-slate-100 rounded-full flex items-center justify-center mb-4">
                   <Cloud size={32} className="text-slate-300" />
                </div>
                <h3 className="text-lg font-medium text-slate-600 mb-1">Bucket Neinițializat sau Gol</h3>
                <p className="max-w-md mx-auto mb-6">
                   Nu există date încărcate în memoria aplicației din <span className="font-mono bg-slate-100 px-1 rounded text-slate-600">date-ap-raw/decizii-cnsc</span>.
                </p>
                <button onClick={simulateSync} className="text-blue-600 font-medium hover:underline flex items-center gap-2">
                   <FolderInput size={16} />
                   Selectează folderul local pentru sincronizare
                </button>
             </div>
          )}
        </div>
      </div>
      <div className="bg-white p-3 border-t border-slate-200 text-xs text-slate-500 flex justify-between px-6">
         <span>Capacitate Browser Utilizată: {(files.reduce((acc, f) => acc + f.content.length, 0) / 1024 / 1024).toFixed(1)} MB</span>
         <span className={activeFiles.length > 10 ? "text-amber-600 font-bold" : "text-green-600"}>
            {activeFiles.length} Active (Limită recomandată: 15)
         </span>
      </div>
    </div>
  );

  const renderDrafter = () => (
    <div className="h-full flex flex-col md:flex-row bg-white">
      <div className="w-full md:w-1/3 border-r border-slate-200 p-6 overflow-y-auto bg-slate-50/50">
        <h2 className="text-lg font-bold text-slate-800 mb-6 flex gap-2 items-center">
          <Scale className="text-blue-600" size={20}/> 
          Configurare Contestație
        </h2>
        
        <div className="space-y-5">
          <div>
            <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Situația de Fapt</label>
            <textarea 
              className="w-full p-3 border border-slate-300 rounded-lg text-sm h-32 focus:ring-2 focus:ring-blue-500 outline-none transition shadow-sm"
              placeholder="Descrie cronologia evenimentelor..."
              value={drafterContext.facts}
              onChange={(e) => setDrafterContext({...drafterContext, facts: e.target.value})}
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Argumentele Autorității</label>
            <textarea 
              className="w-full p-3 border border-slate-300 rounded-lg text-sm h-32 focus:ring-2 focus:ring-blue-500 outline-none transition shadow-sm"
              placeholder="Ce motive a invocat autoritatea pentru respingere?"
              value={drafterContext.authorityArgs}
              onChange={(e) => setDrafterContext({...drafterContext, authorityArgs: e.target.value})}
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-700 uppercase mb-2">Temei Legal</label>
            <input 
              type="text"
              className="w-full p-3 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none transition shadow-sm"
              placeholder="Ex: Art. 215 Legea 98/2016"
              value={drafterContext.legalGrounds}
              onChange={(e) => setDrafterContext({...drafterContext, legalGrounds: e.target.value})}
            />
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
             <div className="prose prose-slate max-w-none font-serif text-slate-800 leading-loose whitespace-pre-wrap bg-white">
                {generatedContent}
             </div>
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

  const renderChat = () => (
    <div className="flex flex-col h-full bg-white">
      <div className="border-b border-slate-100 p-4 flex justify-between items-center bg-white">
         <h2 className="font-bold text-slate-800 flex items-center gap-2">
            <MessageSquare className="text-blue-500" size={18} /> 
            ExpertAP Chat
         </h2>
         <span className="text-xs text-slate-500 bg-slate-100 px-2 py-1 rounded">Context: {activeFiles.length} documente active</span>
      </div>
      <div className="flex-1 overflow-y-auto p-6 space-y-6 bg-slate-50/50">
        {chatMessages.length === 0 && (
           <div className="text-center text-slate-400 mt-20">
             <div className="w-16 h-16 bg-white rounded-2xl shadow-sm flex items-center justify-center mx-auto mb-6">
                <MessageSquare size={32} className="text-blue-500" />
             </div>
             <h3 className="text-slate-800 font-bold mb-2">Cu ce te pot ajuta astăzi?</h3>
             <p className="text-sm max-w-md mx-auto">Pot analiza documentele active din Data Lake sau pot răspunde la întrebări generale despre legislație.</p>
           </div>
        )}
        {chatMessages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-2xl p-5 shadow-sm ${
              msg.role === 'user' 
                ? 'bg-slate-900 text-white rounded-br-none' 
                : 'bg-white border border-slate-200 text-slate-800 rounded-bl-none'
            }`}>
              {msg.text}
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
        <div className="flex gap-2 max-w-4xl mx-auto relative">
          <input 
            type="text" 
            className="flex-1 border border-slate-300 rounded-xl pl-5 pr-12 py-4 focus:ring-2 focus:ring-blue-500 outline-none shadow-sm"
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
        <p className="text-center text-xs text-slate-400 mt-2">Gemini 3 Flash poate face greșeli. Verifică informațiile importante.</p>
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
        {mode === 'redflags' && handleRedFlags && (
          <div className="p-8 max-w-5xl mx-auto h-full overflow-y-auto flex flex-col">
              <header className="mb-6">
                 <h2 className="text-2xl font-bold text-slate-800 flex items-center gap-2"><AlertTriangle className="text-red-500"/> Red Flags Detector</h2>
                 <p className="text-slate-600">Identifică clauze restrictive în documentele active.</p>
              </header>
              
              <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm mb-6">
                 <div className="flex items-center justify-between mb-4">
                    <span className="font-bold text-slate-700">Documente Active: {activeFiles.length}</span>
                    <button 
                      onClick={handleRedFlags}
                      disabled={isLoading || activeFiles.length === 0}
                      className="bg-red-600 text-white px-6 py-2.5 rounded-lg font-medium hover:bg-red-700 transition disabled:opacity-50 flex items-center gap-2"
                    >
                      {isLoading ? <Loader2 className="animate-spin" size={18} /> : "Începe Analiza"}
                    </button>
                 </div>
                 {activeFiles.length === 0 && <p className="text-sm text-red-500">Atenție: Nu ai selectat documente pentru analiză.</p>}
              </div>
              
              <div className="flex-1 bg-slate-50 rounded-xl border border-slate-200 p-6 overflow-y-auto font-mono text-sm whitespace-pre-wrap">
                 {generatedContent || <span className="text-slate-400">Rezultatul analizei va apărea aici...</span>}
              </div>
          </div>
        )}
        {mode === 'clarification' && handleClarification && (
          <div className="p-8 max-w-4xl mx-auto">
             <h2 className="text-2xl font-bold text-slate-800 mb-6 flex items-center gap-2"><Search className="text-purple-600" /> Asistent Clarificări</h2>
             <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200 mb-6">
                <label className="block font-bold text-slate-700 mb-3">Clauza Problematică</label>
                <textarea 
                  className="w-full p-4 border border-slate-300 rounded-lg h-32 mb-4 focus:ring-2 focus:ring-purple-500 outline-none"
                  placeholder="Paste text din documentație..."
                  value={clarificationClause}
                  onChange={(e) => setClarificationClause(e.target.value)}
                />
                <button 
                  onClick={handleClarification}
                  disabled={isLoading || !clarificationClause}
                  className="bg-purple-600 text-white px-6 py-3 rounded-lg font-medium hover:bg-purple-700 transition w-full"
                >
                  {isLoading ? "Generare..." : "Generează Cerere Clarificare"}
                </button>
             </div>
             {generatedContent && (
               <div className="bg-white border border-slate-200 rounded-xl p-8 shadow-sm prose max-w-none whitespace-pre-wrap">
                  {generatedContent}
               </div>
             )}
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
                       <label className="text-sm font-bold text-slate-700 block mb-2">Subiect Memo</label>
                       <textarea 
                          className="w-full border border-slate-300 rounded-lg p-3 text-sm h-24 mb-3 focus:ring-2 focus:ring-teal-500 outline-none"
                          placeholder="Ex: Respingere ofertă..."
                          value={memoTopic}
                          onChange={(e) => setMemoTopic(e.target.value)}
                       />
                       <button 
                          onClick={handleRAGMemo}
                          disabled={isLoading || activeFiles.length === 0}
                          className="w-full bg-teal-600 text-white py-2.5 rounded-lg text-sm font-medium hover:bg-teal-700 transition disabled:opacity-50"
                       >
                          {isLoading ? "Analiză..." : "Generează Memo"}
                       </button>
                       <p className="text-xs text-slate-400 mt-3 text-center">Analizează {activeFiles.length} fișiere active.</p>
                    </div>
                 </div>
                 <div className="flex-1 bg-white border border-slate-200 rounded-xl shadow-sm p-8 overflow-y-auto whitespace-pre-wrap text-slate-800 leading-relaxed">
                    {generatedContent || (
                       <div className="flex flex-col items-center justify-center h-full text-slate-300">
                          <BookOpen size={48} className="mb-4 opacity-20"/>
                          <p>Rezultatul RAG va apărea aici.</p>
                       </div>
                    )}
                 </div>
              </div>
           </div>
        )}
      </main>
    </div>
  );
};

const root = createRoot(document.getElementById("root")!);
root.render(<App />);