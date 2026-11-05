import { H7Shell } from "../h7-shell";
import { CalibrationView } from "../h7-view";
import "../h7.css";
export default function Loading() { return <H7Shell><CalibrationView value={{ state: "loading" }} /></H7Shell>; }
