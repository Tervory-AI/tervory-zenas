import { H7Shell } from "../../h7-shell";
import { BriefView } from "../../h7-view";
import "../../h7.css";
export default function Loading() { return <H7Shell><BriefView value={{ state: "loading" }} /></H7Shell>; }
