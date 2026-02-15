export default function Loading() {
  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="terminal-window max-w-md p-8 text-center">
        <div className="text-neon-cyan text-xl animate-pulse mb-4">
          {">"} LOADING PUBLIC RECORDS...
        </div>
        <div className="text-matrix-green/40 text-sm space-y-1">
          <div className="animate-pulse">FETCHING FEC FILINGS...</div>
          <div className="animate-pulse" style={{ animationDelay: "0.3s" }}>
            LOADING LOBBYING DISCLOSURES...
          </div>
          <div className="animate-pulse" style={{ animationDelay: "0.6s" }}>
            MATCHING VOTING RECORDS...
          </div>
        </div>
        <div className="mt-6 text-matrix-green/20 font-mono">
          {"["}
          <span className="animate-pulse">████████████░░░░</span>
          {"]"}
        </div>
      </div>
    </div>
  );
}
