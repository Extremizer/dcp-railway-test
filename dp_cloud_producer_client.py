"""Signed v2 cloud producer transport. Caller supplies isolated HTTP transport.

The producer neither opens admission nor implements a new cache writer.
The owner token never enters supplier data or persisted logs.
"""
import json
import time
import dp_sync_protocol as protocol


class ProducerContractError(RuntimeError):
    pass


class CloudV2Producer:
    def __init__(self,producer_id,key,send):
        if not producer_id.startswith("cloud-") or len(key)!=32:
            raise ProducerContractError("invalid_producer_configuration")
        self.producer_id=producer_id
        self.key=key
        self.send=send
        self.owner_epoch=None

    def request(self,method,path,data=None):
        body=b"" if data is None else json.dumps(data,separators=(",",":"),allow_nan=False).encode()
        now=int(time.time())
        headers={"X-DP-Protocol":"2","X-DP-Producer-ID":self.producer_id,
                 "X-DP-Sync-Ts":str(now),
                 "X-DP-Sync-Sig":protocol.sign(self.key,method,path,now,self.producer_id,body)}
        if self.owner_epoch and path.endswith("/next"):
            headers["X-DP-Owner-Epoch"]=self.owner_epoch
        return self.send(method,path,body,headers)

    def acquire(self):
        if self.owner_epoch is not None:
            raise ProducerContractError("owner_already_acquired")
        response=self.request("POST","/internal/dp-cloud/owner/acquire",{})
        if response.get("status")!="ACQUIRED" or not response.get("owner_epoch"):
            raise ProducerContractError("owner_not_acquired")
        self.owner_epoch=response["owner_epoch"]
        return response

    def renew(self):
        if not self.owner_epoch:
            raise ProducerContractError("owner_missing")
        result=self.request("POST","/internal/dp-cloud/owner/renew",
                            {"owner_epoch":self.owner_epoch})
        if result.get("status")!="RENEWED":
            raise ProducerContractError("owner_renew_failed")
        return result

    def next(self):
        if not self.owner_epoch:
            raise ProducerContractError("owner_missing")
        return self.request("GET","/internal/dp-cloud/next")["request"]

    def complete(self,claim,result):
        if not self.owner_epoch:
            raise ProducerContractError("owner_missing")
        payload={**result,"protocol_version":2,"request_id":claim["request_id"],
                 "producer_id":self.producer_id,"claim_token":claim["claim_token"],
                 "claim_generation":claim["claim_generation"],
                 "owner_epoch":self.owner_epoch}
        ack=self.request("POST","/internal/dp-cloud/result",payload)
        if ack.get("ok") is not True or ack.get("status")!=result["status"] or (
            ack.get("server_cache_committed") is not (result["status"]=="FOUND")
        ):
            raise ProducerContractError("completion_ack_unproven")
        return ack

    def release(self):
        if not self.owner_epoch:
            raise ProducerContractError("owner_missing")
        result=self.request("POST","/internal/dp-cloud/owner/release",
                            {"owner_epoch":self.owner_epoch})
        if result.get("status")!="RELEASED":
            raise ProducerContractError("owner_release_failed")
        self.owner_epoch=None
        return result
