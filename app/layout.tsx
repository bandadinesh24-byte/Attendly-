import './globals.css';
import type { Metadata } from 'next';
export const metadata: Metadata = { title: 'Attendly · AI Attendance Monitor', description: 'Attendance anomaly monitoring and investigation' };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="en"><body>{children}</body></html>; }
