import time
import secrets
from typing import Any, Optional
from core.domain.entities import Block, calculate_merkle_root
from core.security import signaturedata, get_device_id

class BlockFactory:
    @staticmethod
    def create_genesis_block(device_id: Optional[str] = None) -> Block:
        ts = time.time()
        nonce = secrets.token_hex(16)
        device = device_id or get_device_id()
        msg = f"0|{ts}|Genesis Block|0|{nonce}"
        sig = signaturedata(msg, device)

        return Block(
            index=0,
            timestamp=ts,
            data={
                "type": "genesis",
                "message": "PsikoNot — Genesis Block",
                "created_by": "system",
                "device_id": device,
            },
            previous_hash="0",
            signature=sig,
            nonce=nonce,
            device_id=device,
            merkle_root=None,
        )

    @staticmethod
    def create_data_block(
        index: int,
        previous_hash: str,
        data: Any,
        is_protected: bool = False,
        protection_hash: Optional[str] = None,
        device_id: Optional[str] = None,
    ) -> Block:
        ts = time.time()
        nonce = secrets.token_hex(16)
        device = device_id or get_device_id()

        merkle_root = calculate_merkle_root(data)
        msg = f"{index}|{ts}|{merkle_root}|{previous_hash}|{nonce}"
        sig = signaturedata(msg, device)

        return Block(
            index=index,
            timestamp=ts,
            data=data,
            previous_hash=previous_hash,
            signature=sig,
            is_protected=is_protected,
            protection_hash=protection_hash,
            nonce=nonce,
            device_id=device,
            merkle_root=merkle_root,
        )

    @staticmethod
    def create_correction_block(
        index: int,
        previous_hash: str,
        corrected_block_index: int,
        corrected_data: Any,
        username: str,
        reason: str = "",
        device_id: Optional[str] = None,
    ) -> Block:
        device = device_id or get_device_id()
        correction_info = {
            "type": "correction",
            "correction_of": corrected_block_index,
            "corrected_data": corrected_data,
            "note": "Correction block — does not overwrite the previous record.",
            "corrected_by": username,
            "reason": reason,
            "corrected_at": time.time(),
        }

        return BlockFactory.create_data_block(
            index=index,
            previous_hash=previous_hash,
            data=correction_info,
            is_protected=False,
            protection_hash=None,
            device_id=device,
        )

    @staticmethod
    def create_audit_block(
        index: int,
        previous_hash: str,
        action: str,
        username: str,
        target_block_index: Optional[int] = None,
        extra: Optional[dict] = None,
        device_id: Optional[str] = None,
    ) -> Block:
        device = device_id or get_device_id()
        audit_data = {
            "type": "audit",
            "action": action,
            "username": username,
            "target_block_index": target_block_index,
            "device_id": device,
            **(extra or {})
        }

        return BlockFactory.create_data_block(
            index=index,
            previous_hash=previous_hash,
            data=audit_data,
            is_protected=False,
            protection_hash=None,
            device_id=device,
        )
