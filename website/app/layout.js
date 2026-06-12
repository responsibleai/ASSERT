import "./globals.css";

export const metadata = {
  title: "Assert",
  description: "Adaptive Spec-driven Scoring for Evaluation and Regression Testing"
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover"
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
