import Foundation from "./pages/Foundation";
import { useDocumentTitle } from "./hooks/useDocumentTitle";

export default function App() {
  useDocumentTitle("SpotM3U");

  return <Foundation />;
}
