export default function SignInLayout({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", placeItems: "center", minHeight: "70vh" }}>
      {children}
    </div>
  );
}
