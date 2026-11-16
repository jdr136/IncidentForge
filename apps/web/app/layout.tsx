import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'IncidentForge',
  description: 'AI incident response evaluation lab',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav className="nav">
          <span className="nav-logo">⚡ IncidentForge</span>
          <a href="/" className="nav-link">Scenarios</a>
          <a href="/benchmarks" className="nav-link">Benchmarks</a>
        </nav>
        {children}
      </body>
    </html>
  );
}
