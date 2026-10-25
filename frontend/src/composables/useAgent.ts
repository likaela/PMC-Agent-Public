import { createAgentStore } from "./agentStore";
import { createLiveService } from "../services/apiClient";
// Saved legacy mock preferences are intentionally ignored. There is one data path.
const store = createAgentStore(createLiveService());
export const useAgent = () => store;
