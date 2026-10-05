import logging
import hashlib
import hmac
import json
from typing import Any, Optional, Dict, List
from core.domain.entities import Block
from core.domain.factories import BlockFactory
from core.ports.repositories import IBlockRepository
from core.ports.cryptography import IEncryptionStrategy
from core.security import (
    verify_block_password,
    hash_block_password,
    verify_message,
    get_device_id,
    derive_rest_secret,
)
from core.events.event_bus import event_bus, RecordAddedEvent, RecordReadEvent
from core.pseudonymization.service import project_name_for, get_pseudonymization_service
from core.services.erasure_service import get_erasure_key_store

logger = logging.getLogger("vhv.recordservice")

# Marks a value that is AES-256 encrypted at rest under the server's KMS key.
# The marker keeps the reveal path unambiguous and never collides with legacy
# plaintext (a dict) or password-protected ciphertext (stored with is_protected).
_REST_PREFIX = "vhv-rest:"

class RecordService:
    def __init__(self, block_repo: IBlockRepository, crypto_strategy: IEncryptionStrategy):
        self.block_repo = block_repo
        self.crypto_strategy = crypto_strategy

    def _get_project_name(self, patient_id: str) -> str:
        # The clinical chain store is namespaced by the deterministic pseudonym,
        # never the raw patient id — see core.pseudonymization.service.
        return project_name_for(patient_id)

    # ── At-rest encryption (server-held KMS key) ────────────────────────
    def _rest_key(self, patient_id: str, create: bool) -> Optional[str]:
        """
        The at-rest secret: the KMS root-derived secret mixed with the patient's
        erasure secret. Returns None once the patient has been erased (their
        erasure secret is destroyed), which makes decryption impossible by design.
        On the write path ``create=True`` mints the erasure secret on first use;
        the read path never creates one.
        """
        store = get_erasure_key_store()
        erasure = store.get_or_create(patient_id) if create else store.get(patient_id)
        if erasure is None:
            return None
        root = derive_rest_secret(patient_id)
        return hmac.new(erasure, root.encode("utf-8"), hashlib.sha256).hexdigest()

    def _encrypt_at_rest(self, patient_id: str, index: int, payload: Any) -> str:
        """Encrypt a clinical payload for storage; returns a prefixed ciphertext."""
        project_name = self._get_project_name(patient_id)
        payload_str = (
            json.dumps(payload, sort_keys=True, ensure_ascii=False)
            if isinstance(payload, dict)
            else str(payload)
        )
        secret = self._rest_key(patient_id, create=True)
        patient_salt = self.block_repo.get_patient_salt(project_name)
        ciphertext, salt = self.crypto_strategy.encrypt_data(payload_str, secret, patient_salt)
        self.block_repo.save_block_salt(project_name, index, salt)
        return _REST_PREFIX + ciphertext

    def _reveal(self, patient_id: str, index: int, value: Any) -> Any:
        """
        Return the plaintext for a stored value.

        At-rest ciphertext (prefixed) is decrypted with the server key. Anything
        else — legacy plaintext dicts, or password-protected ciphertext handled by
        the password path — passes through untouched.
        """
        if not (isinstance(value, str) and value.startswith(_REST_PREFIX)):
            return value
        project_name = self._get_project_name(patient_id)
        salt = self.block_repo.load_block_salt(project_name, index)
        if not salt:
            return value
        secret = self._rest_key(patient_id, create=False)
        if secret is None:
            # The patient's erasure key is destroyed — the content is
            # cryptographically shredded and can never be recovered.
            return {"__erased__": True,
                    "reason": "Record cryptographically erased (GDPR/KVKK Art. 17)"}
        try:
            decrypted = self.crypto_strategy.decrypt_data(value[len(_REST_PREFIX):], secret, salt)
        except Exception:
            # Prefixed ciphertext that will not decrypt (wrong key / corruption).
            # Return an explicit marker rather than the raw ciphertext, so callers
            # never mistake unreadable bytes for real clinical data.
            return {"__unreadable__": True,
                    "reason": "Record could not be decrypted (wrong key or corrupted data)"}
        try:
            return json.loads(decrypted)
        except Exception:
            return decrypted

    def _get_or_create_chain(self, patient_id: str) -> List[Block]:
        project_name = self._get_project_name(patient_id)
        if not self.block_repo.project_exists(project_name):
            self.block_repo.create_project(project_name)

        chain = self.block_repo.load_all_blocks(project_name)
        if not chain:
            # Seed the genesis block. Do NOT reset the store here: this runs inside
            # the caller's unit of work, and dropping the environment underneath an
            # open transaction fails the very first write to a new patient chain.
            genesis = BlockFactory.create_genesis_block()
            self.block_repo.save_block(project_name, genesis)
            chain = [genesis]
        return chain

    def get_chain(self, patient_id: str) -> List[Block]:
        return self._get_or_create_chain(patient_id)

    def _anchor_chain(self, patient_id: str) -> None:
        """
        Re-anchors the Merkle root of the patient chain.

        The root must be computed over the chain as it is *committed*: inside an
        open unit of work a reader still sees the pre-write snapshot, which would
        anchor a root that is one write behind and leave the chain permanently
        reporting "Merkle root mismatch".
        """
        def anchor():
            try:
                from core.services.notarizer import BlockchainNotarizer
                BlockchainNotarizer(self.block_repo).notarize_patient_chain(patient_id)
            except Exception as e:
                logger.error(f"[Notarizer Warning] Notarization trigger failed: {e}")

        import database.storage as storage
        if not storage.run_after_commit(anchor):
            anchor()

    def add_record(
        self,
        patient_id: str,
        data: Any,
        is_protected: bool = False,
        protection_password: Optional[str] = None,
        username: str = "system",
    ) -> Block:
        # Persist the identity↔pseudonym mapping on the write path so the real id
        # can still be resolved by an authorized admin and destroyed for erasure.
        get_pseudonymization_service().pseudonymize(patient_id)

        project_name = self._get_project_name(patient_id)
        chain = self._get_or_create_chain(patient_id)
        last_block = chain[-1]
        index = last_block.index + 1
        previous_hash = last_block.hash

        data_to_store = data
        protection_hash = None

        if is_protected and protection_password:
            # Password-protected: encrypted under a key the server never holds.
            protection_hash = hash_block_password(protection_password)
            payload_str = (
                json.dumps(data, sort_keys=True, ensure_ascii=False)
                if isinstance(data, dict)
                else str(data)
            )
            patient_salt = self.block_repo.get_patient_salt(project_name)
            encrypted_str, salt = self.crypto_strategy.encrypt_data(
                payload_str, protection_password, patient_salt
            )
            data_to_store = encrypted_str
            self.block_repo.save_block_salt(project_name, index, salt)
        else:
            # Default: encrypted at rest under the server's KMS key, so the chain
            # store never holds plaintext clinical data.
            data_to_store = self._encrypt_at_rest(patient_id, index, data)

        block = BlockFactory.create_data_block(
            index=index,
            previous_hash=previous_hash,
            data=data_to_store,
            is_protected=is_protected,
            protection_hash=protection_hash,
        )

        self.block_repo.save_block(project_name, block)

        if is_protected and protection_hash:
            self.block_repo.save_block_pwd_hash(project_name, index, protection_hash)

        # Publish decouple audit log events
        event_bus.publish(RecordAddedEvent(
            project_name=project_name,
            username=username,
            block_index=index,
            device_id=block.device_id,
            is_protected=is_protected
        ))

        # Create audit block on chain
        audit_index = index + 1
        audit_block = BlockFactory.create_audit_block(
            index=audit_index,
            previous_hash=block.hash,
            action="BLOCK_ADDED",
            username=username,
            target_block_index=index,
            extra={"is_protected": is_protected}
        )
        self.block_repo.save_block(project_name, audit_block)

        self._anchor_chain(patient_id)

        return block

    def add_correction_block(
        self,
        patient_id: str,
        block_index: int,
        corrected_data: Any,
        encryption_password: Optional[str] = None,
        username: str = "system",
        reason: str = "",
    ) -> Block:
        project_name = self._get_project_name(patient_id)
        chain = self._get_or_create_chain(patient_id)
        last_block = chain[-1]
        index = last_block.index + 1
        previous_hash = last_block.hash

        data_content = corrected_data

        if encryption_password:
            payload_str = (
                json.dumps(corrected_data, sort_keys=True, ensure_ascii=False)
                if isinstance(corrected_data, dict)
                else str(corrected_data)
            )
            patient_salt = self.block_repo.get_patient_salt(project_name)
            encrypted_str, salt = self.crypto_strategy.encrypt_data(
                payload_str, encryption_password, patient_salt
            )
            data_content = encrypted_str
            self.block_repo.save_block_salt(project_name, index, salt)
        else:
            # The correction wrapper stays a readable dict so the chain can resolve
            # it; only the corrected clinical payload is encrypted at rest.
            data_content = self._encrypt_at_rest(patient_id, index, corrected_data)

        block = BlockFactory.create_correction_block(
            index=index,
            previous_hash=previous_hash,
            corrected_block_index=block_index,
            corrected_data=data_content,
            username=username,
            reason=reason,
        )

        self.block_repo.save_block(project_name, block)

        event_bus.publish(RecordReadEvent(
            project_name=project_name,
            username=username,
            block_index=index,
            device_id=block.device_id,
            action="CORRECTION_ADDED",
            extra={"correction_of": block_index}
        ))

        # Create audit block on chain
        audit_index = index + 1
        audit_block = BlockFactory.create_audit_block(
            index=audit_index,
            previous_hash=block.hash,
            action="CORRECTION_ADDED",
            username=username,
            target_block_index=index,
            extra={"correction_of": block_index}
        )
        self.block_repo.save_block(project_name, audit_block)

        self._anchor_chain(patient_id)

        return block

    def get_block_data(
        self,
        patient_id: str,
        block_index: int,
        password: Optional[str] = None,
        username: str = "anonymous",
    ) -> Any:
        project_name = self._get_project_name(patient_id)
        chain = self._get_or_create_chain(patient_id)

        block = next((b for b in chain if b.index == block_index), None)
        if not block:
            return None

        event_bus.publish(RecordReadEvent(
            project_name=project_name,
            username=username,
            block_index=block_index,
            device_id=get_device_id(),
            action="BLOCK_READ_ATTEMPT"
        ))

        if block.is_protected:
            if not password:
                return "SECURE — password required"

            stored_hash = self.block_repo.load_block_pwd_hash(project_name, block_index)
            if not stored_hash or not verify_block_password(password, stored_hash):
                event_bus.publish(RecordReadEvent(
                    project_name=project_name,
                    username=username,
                    block_index=block_index,
                    device_id=get_device_id(),
                    action="BLOCK_READ_FAILED",
                    extra={"reason": "WRONG_PASSWORD"}
                ))
                return "INCORRECT PASSWORD"

            salt = self.block_repo.load_block_salt(project_name, block_index)
            if not salt:
                return "Salt not found — data integrity error"

            try:
                decrypted_str = self.crypto_strategy.decrypt_data(block.data, password, salt)
            except Exception as e:
                event_bus.publish(RecordReadEvent(
                    project_name=project_name,
                    username=username,
                    block_index=block_index,
                    device_id=get_device_id(),
                    action="BLOCK_READ_FAILED",
                    extra={"reason": f"DECRYPTION_ERROR: {str(e)}"}
                ))
                return f"DECRYPTION ERROR: {str(e)}"

            try:
                result = json.loads(decrypted_str)
            except Exception:
                result = decrypted_str

            event_bus.publish(RecordReadEvent(
                project_name=project_name,
                username=username,
                block_index=block_index,
                device_id=get_device_id(),
                action="BLOCK_READ_SUCCESS"
            ))
            return result

        # Unprotected (may be encrypted at rest under the server key)
        event_bus.publish(RecordReadEvent(
            project_name=project_name,
            username=username,
            block_index=block_index,
            device_id=get_device_id(),
            action="BLOCK_READ_SUCCESS"
        ))
        return self._reveal(patient_id, block_index, block.data)

    def get_final_block_data(
        self,
        patient_id: str,
        block_index: int,
        password: Optional[str] = None,
        username: str = "anonymous",
    ) -> Any:
        project_name = self._get_project_name(patient_id)
        chain = self._get_or_create_chain(patient_id)

        block = next((b for b in chain if b.index == block_index), None)
        if not block:
            return None

        # Find latest correction block if any
        correction_block = None
        for block in reversed(chain):
            if (
                isinstance(block.data, dict)
                and block.data.get("type") == "correction"
                and block.data.get("correction_of") == block_index
            ):
                correction_block = block
                break

        if not correction_block:
            return self.get_block_data(patient_id, block_index, password, username)

        event_bus.publish(RecordReadEvent(
            project_name=project_name,
            username=username,
            block_index=block_index,
            device_id=get_device_id(),
            action="BLOCK_READ_ATTEMPT",
            extra={"correction_index": correction_block.index}
        ))

        original_block = block
        corrected_data = correction_block.data["corrected_data"]

        if original_block.is_protected:
            if not password:
                return "SECURE — password required"

            stored_hash = self.block_repo.load_block_pwd_hash(project_name, block_index)
            if not stored_hash or not verify_block_password(password, stored_hash):
                event_bus.publish(RecordReadEvent(
                    project_name=project_name,
                    username=username,
                    block_index=block_index,
                    device_id=get_device_id(),
                    action="BLOCK_READ_FAILED",
                    extra={"reason": "WRONG_PASSWORD", "correction_index": correction_block.index}
                ))
                return "INCORRECT PASSWORD"

            salt = self.block_repo.load_block_salt(project_name, correction_block.index)
            if not salt:
                return "Salt not found — data integrity error"

            try:
                decrypted_str = self.crypto_strategy.decrypt_data(corrected_data, password, salt)
            except Exception as e:
                event_bus.publish(RecordReadEvent(
                    project_name=project_name,
                    username=username,
                    block_index=block_index,
                    device_id=get_device_id(),
                    action="BLOCK_READ_FAILED",
                    extra={"reason": f"DECRYPTION_ERROR: {str(e)}", "correction_index": correction_block.index}
                ))
                return f"DECRYPTION ERROR: {str(e)}"

            try:
                result = json.loads(decrypted_str)
            except Exception:
                result = decrypted_str

            event_bus.publish(RecordReadEvent(
                project_name=project_name,
                username=username,
                block_index=block_index,
                device_id=get_device_id(),
                action="BLOCK_READ_SUCCESS",
                extra={"correction_index": correction_block.index}
            ))
            return result

        # Unprotected block correction (corrected payload may be encrypted at rest)
        event_bus.publish(RecordReadEvent(
            project_name=project_name,
            username=username,
            block_index=block_index,
            device_id=get_device_id(),
            action="BLOCK_READ_SUCCESS",
            extra={"correction_index": correction_block.index}
        ))
        return self._reveal(patient_id, correction_block.index, corrected_data)

    def get_final_data(self, patient_id: str) -> Dict[int, Any]:
        chain = self._get_or_create_chain(patient_id)
        result: Dict[int, Any] = {}

        for block in chain:
            if isinstance(block.data, dict) and block.data.get("type") == "correction":
                continue
            # Decrypt at-rest records; password-protected ciphertext and legacy
            # plaintext pass through and are handled by the query layer.
            result[block.index] = self._reveal(patient_id, block.index, block.data)

        for block in chain:
            if isinstance(block.data, dict) and block.data.get("type") == "correction":
                target = block.data.get("correction_of")
                if target is not None and target in result:
                    result[target] = self._reveal(
                        patient_id, block.index, block.data["corrected_data"]
                    )

        return result

    def get_corrections_index(self, patient_id: str) -> Dict[int, dict]:
        """
        Map each corrected record's index to its latest correction's provenance.

        The correction wrapper (type/correction_of/reason/corrected_by) is stored
        in the clear — only the corrected clinical payload is encrypted — so this
        needs no decryption. The record itself is never overwritten; this simply
        tells the read layer that a later block supersedes it and by whom/why.
        """
        chain = self._get_or_create_chain(patient_id)
        corrections: Dict[int, dict] = {}
        for block in chain:
            if isinstance(block.data, dict) and block.data.get("type") == "correction":
                target = block.data.get("correction_of")
                if target is None:
                    continue
                # Keep the most recent correction (highest correction block index).
                existing = corrections.get(target)
                if existing and existing["correction_index"] >= block.index:
                    continue
                corrections[target] = {
                    "correction_index": block.index,
                    "corrected_by": block.data.get("corrected_by", ""),
                    "corrected_at": block.data.get("corrected_at"),
                    "reason": block.data.get("reason", ""),
                }
        return corrections

    def get_original_block_data(self, patient_id: str, block_index: int) -> Any:
        """Return a record's pre-correction content — the original never changes."""
        return self.get_block_data(patient_id, block_index)

    def is_chain_valid(self, patient_id: str) -> bool:
        return self.find_broken_link_index(patient_id) == -1

    def find_broken_link_index(self, patient_id: str) -> int:
        """
        Return the index of the first block that fails verification, or -1 if the
        whole chain is intact. Each block must satisfy four invariants:

          1. its own ``hash`` recomputes (block-field integrity),
          2. its ``previous_hash`` equals the prior block's ``hash`` (the chain
             link — this is where the hash chain actually lives),
          3. its timestamp does not go backwards and its nonce is unique (replay),
          4. its ``signature`` verifies as an HMAC over the signed fields
             (authenticity — only the signing-key holder could have produced it).
        """
        chain = self._get_or_create_chain(patient_id)
        seen_nonces = set()
        for i in range(1, len(chain)):
            prev = chain[i - 1]
            curr = chain[i]

            # (1) the block's own identity digest must recompute
            if curr.hash != curr.create_hash():
                return i

            # (2) the chain link: this block points at the previous block's hash
            if curr.previous_hash != prev.hash:
                return i

            if curr.timestamp < prev.timestamp:
                return i

            nonce_key = (curr.timestamp, curr.nonce)
            if nonce_key in seen_nonces:
                return i
            seen_nonces.add(nonce_key)

            if curr.merkle_root is not None:
                msg = f"{curr.index}|{curr.timestamp}|{curr.merkle_root}|{curr.previous_hash}|{curr.nonce}"
            else:
                data_text = (
                    json.dumps(curr.data, sort_keys=True, ensure_ascii=False)
                    if isinstance(curr.data, dict)
                    else str(curr.data)
                )
                msg = f"{curr.index}|{curr.timestamp}|{data_text}|{curr.previous_hash}|{curr.nonce}"

            if not verify_message(msg, curr.signature, curr.device_id):
                return i

        return -1
