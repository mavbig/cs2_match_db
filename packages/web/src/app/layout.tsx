import "./globals.css";
import { Nav } from "@/components/Nav";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Nav />
        <main className="container" style={{ paddingTop: "1.5rem", paddingBottom: "3rem" }}>
          {children}
        </main>
      </body>
    </html>
  );
}
