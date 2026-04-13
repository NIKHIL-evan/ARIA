from aria.memory.repo_reader import CodeChunk
from typing import Dict, List ,Tuple

class SyncManager:
    def compute_deltas(
            self,
            existing_state: Dict[str,str],  # {Chunk_id : content_hash} from qdrant
            incoming_chunk: List[CodeChunk] 
    ) -> Tuple[List[CodeChunk], List[CodeChunk], List[str]]:
        """
        Compares incoming memory against existing memory to find deltas.
        Returns: (to_add, to_update, to_delete_ids)
        """
        to_add = []
        to_update = []
        to_delete_ids = []

        incoming_map = { chunks.id : chunks for chunks in incoming_chunk}

        for inc_id, inc_chunk in incoming_map.items():
            if inc_id not in existing_state:
                to_add.append(inc_chunk)
            elif existing_state[inc_id] != inc_chunk.content_hash:
                to_update.append(inc_chunk)

        for ext_id in existing_state.keys():
            if ext_id not in incoming_map:
                to_delete_ids.append(ext_id)
        
        return to_add, to_update, to_delete_ids

